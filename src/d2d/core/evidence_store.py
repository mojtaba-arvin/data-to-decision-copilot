"""SQLite-backed append-only evidence journal with a verifiable hash chain."""
import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from d2d.core.contracts import Evidence, EvidenceStatus
from d2d.core.journal_backup import JournalBackupReport, create_verified_backup

SCHEMA_VERSION = 1
GENESIS = "0" * 64


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _validate_evidence(evidence: Evidence) -> None:
    if evidence.status is not EvidenceStatus.EXECUTED:
        raise ValueError("only executed evidence can be persisted")
    if hashlib.sha256(evidence.sql.encode("utf-8")).hexdigest() != evidence.query_sha256:
        raise ValueError("evidence query digest does not match SQL")
    result = {
        "columns": evidence.columns,
        "rows": evidence.rows,
        "captured_rows": evidence.row_count,
        "output_bytes": evidence.output_bytes,
        "truncated": evidence.truncated,
    }
    if _digest(result) != evidence.result_sha256:
        raise ValueError("evidence result digest does not match captured output")


@dataclass(frozen=True, slots=True)
class VerificationReport:
    valid: bool
    records_checked: int
    error: str | None = None


class SQLiteEvidenceStore:
    """Durable local journal; UPDATE and DELETE are blocked by SQLite triggers."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS journal_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evidence_journal (
                    sequence INTEGER PRIMARY KEY,
                    evidence_id TEXT NOT NULL UNIQUE,
                    payload TEXT NOT NULL,
                    record_sha256 TEXT NOT NULL,
                    previous_sha256 TEXT NOT NULL,
                    entry_sha256 TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS evidence_no_update
                BEFORE UPDATE ON evidence_journal
                BEGIN SELECT RAISE(ABORT, 'evidence journal is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS evidence_no_delete
                BEFORE DELETE ON evidence_journal
                BEGIN SELECT RAISE(ABORT, 'evidence journal is append-only'); END;
                """
            )
            row = connection.execute(
                "SELECT value FROM journal_meta WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO journal_meta(key, value) VALUES (?, ?)",
                    ("schema_version", str(SCHEMA_VERSION)),
                )
            elif row[0] != str(SCHEMA_VERSION):
                raise RuntimeError(f"unsupported evidence journal schema version: {row[0]}")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def append(self, evidence: Evidence) -> int:
        """Atomically append a validated record and return its sequence number."""
        _validate_evidence(evidence)
        payload_text = _canonical(evidence.model_dump(mode="json")).decode("utf-8")
        record_hash = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            last = connection.execute(
                "SELECT sequence, entry_sha256 FROM evidence_journal "
                "ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            sequence = 1 if last is None else int(last["sequence"]) + 1
            previous = GENESIS if last is None else str(last["entry_sha256"])
            entry_hash = _digest(
                {
                    "schema_version": SCHEMA_VERSION,
                    "sequence": sequence,
                    "evidence_id": evidence.evidence_id,
                    "record_sha256": record_hash,
                    "previous_sha256": previous,
                }
            )
            connection.execute(
                """
                INSERT INTO evidence_journal
                (sequence, evidence_id, payload, record_sha256, previous_sha256, entry_sha256)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    sequence,
                    evidence.evidence_id,
                    payload_text,
                    record_hash,
                    previous,
                    entry_hash,
                ),
            )
            connection.commit()
        return sequence

    def get(self, evidence_id: str) -> Evidence | None:
        """Return an immutable record by ID."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT payload FROM evidence_journal WHERE evidence_id = ?",
                (evidence_id,),
            ).fetchone()
        return None if row is None else Evidence.model_validate_json(row["payload"])

    def count(self) -> int:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM evidence_journal"
            ).fetchone()
        return int(row["total"])

    def backup(self, destination: str | Path) -> JournalBackupReport:
        """Create a verified, atomic snapshot without modifying the live journal."""
        report = self.verify()
        if not report.valid:
            raise ValueError(f"cannot back up an invalid evidence journal: {report.error}")
        return create_verified_backup(
            self.path, destination,
            verify=lambda path: SQLiteEvidenceStore(path).verify(),
            table_name="evidence_journal",
        )

    def verify(self) -> VerificationReport:
        """Recompute evidence, record, sequence, and complete chain digests."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT sequence, evidence_id, payload, record_sha256, "
                "previous_sha256, entry_sha256 FROM evidence_journal ORDER BY sequence"
            ).fetchall()

        previous = GENESIS
        for expected, row in enumerate(rows, start=1):
            if row["sequence"] != expected:
                return VerificationReport(False, expected - 1, "non-contiguous sequence")
            if row["previous_sha256"] != previous:
                return VerificationReport(False, expected - 1, "previous-entry link mismatch")
            try:
                evidence = Evidence.model_validate_json(row["payload"])
                _validate_evidence(evidence)
            except (ValueError, TypeError) as exc:
                return VerificationReport(False, expected - 1, f"invalid evidence: {exc}")
            if evidence.evidence_id != row["evidence_id"]:
                return VerificationReport(False, expected - 1, "evidence ID mismatch")
            record_hash = hashlib.sha256(row["payload"].encode("utf-8")).hexdigest()
            if record_hash != row["record_sha256"]:
                return VerificationReport(False, expected - 1, "record digest mismatch")
            entry_hash = _digest(
                {
                    "schema_version": SCHEMA_VERSION,
                    "sequence": expected,
                    "evidence_id": evidence.evidence_id,
                    "record_sha256": record_hash,
                    "previous_sha256": previous,
                }
            )
            if entry_hash != row["entry_sha256"]:
                return VerificationReport(False, expected - 1, "entry digest mismatch")
            previous = entry_hash
        return VerificationReport(True, len(rows))
