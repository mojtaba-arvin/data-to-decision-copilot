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


def _read_checkpoint(connection: sqlite3.Connection) -> tuple[int, str]:
    rows = connection.execute(
        "SELECT key, value FROM journal_meta WHERE key IN "
        "('checkpoint_schema_version', 'checkpoint_sequence', 'checkpoint_sha256')"
    ).fetchall()
    metadata = {str(row["key"]): str(row["value"]) for row in rows}
    expected = {"checkpoint_schema_version", "checkpoint_sequence", "checkpoint_sha256"}
    if not metadata:
        return 0, GENESIS
    if set(metadata) != expected or metadata["checkpoint_schema_version"] != "1":
        raise RuntimeError("invalid or unsupported evidence journal checkpoint metadata")
    try:
        sequence = int(metadata["checkpoint_sequence"])
        digest = metadata["checkpoint_sha256"]
        if sequence < 0 or len(digest) != 64:
            raise ValueError
        if any(character not in "0123456789abcdef" for character in digest):
            raise ValueError
    except ValueError as exc:
        raise RuntimeError("invalid evidence journal checkpoint") from exc
    if sequence == 0 and digest != GENESIS:
        raise RuntimeError("genesis checkpoint hash mismatch")
    return sequence, digest


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

    def __init__(
        self,
        path: str | Path,
        *,
        trusted_checkpoint: tuple[int, str] | None = None,
    ) -> None:
        self.path = str(path)
        self.trusted_checkpoint = trusted_checkpoint
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

    def initialize_checkpoint_metadata(self) -> tuple[int, str]:
        """Install additive checkpoint metadata without changing journal records.

        Existing databases remain genesis-anchored until a future verified rotation
        atomically advances this checkpoint. This method never deletes or rewrites rows.
        """
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                report = self.verify()
                if not report.valid:
                    raise ValueError(
                        f"cannot initialize checkpoint metadata on an invalid journal: {report.error}"
                    )
                sequence, digest = _read_checkpoint(connection)
                connection.execute(
                    "INSERT OR IGNORE INTO journal_meta(key, value) VALUES (?, ?)",
                    ("checkpoint_schema_version", "1"),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO journal_meta(key, value) VALUES (?, ?)",
                    ("checkpoint_sequence", str(sequence)),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO journal_meta(key, value) VALUES (?, ?)",
                    ("checkpoint_sha256", digest),
                )
                _read_checkpoint(connection)
                connection.commit()
                return sequence, digest
            except Exception:
                connection.rollback()
                raise

    def append(self, evidence: Evidence) -> int:
        """Atomically append a validated record and return its sequence number."""
        _validate_evidence(evidence)
        payload_text = _canonical(evidence.model_dump(mode="json")).decode("utf-8")
        record_hash = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            checkpoint_sequence, checkpoint_hash = _read_checkpoint(connection)
            if checkpoint_sequence > 0 and self.trusted_checkpoint != (
                checkpoint_sequence, checkpoint_hash
            ):
                raise RuntimeError(
                    "non-genesis evidence checkpoint requires a trusted archive checkpoint"
                )
            if self.trusted_checkpoint is not None and self.trusted_checkpoint != (
                checkpoint_sequence, checkpoint_hash
            ):
                raise RuntimeError("trusted evidence checkpoint does not match journal metadata")
            first = connection.execute(
                "SELECT sequence, previous_sha256 FROM evidence_journal "
                "ORDER BY sequence ASC LIMIT 1"
            ).fetchone()
            if first is not None and (
                int(first["sequence"]) != checkpoint_sequence + 1
                or str(first["previous_sha256"]) != checkpoint_hash
            ):
                raise RuntimeError("evidence journal checkpoint does not match first stored record")
            last = connection.execute(
                "SELECT sequence, entry_sha256 FROM evidence_journal "
                "ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            sequence = (
                checkpoint_sequence + 1 if last is None else int(last["sequence"]) + 1
            )
            previous = checkpoint_hash if last is None else str(last["entry_sha256"])
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
            verify=lambda path: SQLiteEvidenceStore(
                path, trusted_checkpoint=self.trusted_checkpoint
            ).verify(),
            table_name="evidence_journal",
        )

    def verify(self) -> VerificationReport:
        """Verify the active journal from its persisted predecessor checkpoint."""
        with closing(self._connect()) as connection:
            checkpoint_sequence, checkpoint_hash = _read_checkpoint(connection)
            if checkpoint_sequence > 0 and self.trusted_checkpoint != (
                checkpoint_sequence, checkpoint_hash
            ):
                return VerificationReport(
                    False, 0, "non-genesis checkpoint requires a trusted archive checkpoint"
                )
            if self.trusted_checkpoint is not None and self.trusted_checkpoint != (
                checkpoint_sequence, checkpoint_hash
            ):
                return VerificationReport(
                    False, 0, "trusted checkpoint does not match journal metadata"
                )
            count = int(
                connection.execute("SELECT COUNT(*) FROM evidence_journal").fetchone()[0]
            )
        if count == 0 and checkpoint_sequence > 0:
            return VerificationReport(True, 0)
        return self.verify_segment(
            start_sequence=checkpoint_sequence + 1,
            previous_sha256=checkpoint_hash,
        )

    def verify_segment(
        self, *, start_sequence: int, previous_sha256: str
    ) -> VerificationReport:
        """Verify a contiguous journal segment against an explicit predecessor checkpoint.

        This validates record integrity and sequence/hash continuity within this
        database. The caller remains responsible for authenticating the checkpoint
        through the preceding archive manifest or another trusted anchor.
        """
        if start_sequence < 1:
            raise ValueError("start_sequence must be positive")
        if len(previous_sha256) != 64:
            raise ValueError("previous_sha256 must be a SHA-256 digest")
        try:
            int(previous_sha256, 16)
        except ValueError as exc:
            raise ValueError("previous_sha256 must be a SHA-256 digest") from exc

        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT sequence, evidence_id, payload, record_sha256, "
                "previous_sha256, entry_sha256 FROM evidence_journal ORDER BY sequence"
            ).fetchall()

        if not rows:
            if start_sequence == 1 and previous_sha256 == GENESIS:
                return VerificationReport(True, 0)
            return VerificationReport(False, 0, "journal segment must not be empty")

        previous = previous_sha256
        for expected, row in enumerate(rows, start=start_sequence):
            if row["sequence"] != expected:
                return VerificationReport(False, expected - start_sequence, "non-contiguous sequence")
            if row["previous_sha256"] != previous:
                return VerificationReport(False, expected - start_sequence, "previous-entry link mismatch")
            try:
                evidence = Evidence.model_validate_json(row["payload"])
                _validate_evidence(evidence)
            except (ValueError, TypeError) as exc:
                return VerificationReport(False, expected - start_sequence, f"invalid evidence: {exc}")
            if evidence.evidence_id != row["evidence_id"]:
                return VerificationReport(False, expected - start_sequence, "evidence ID mismatch")
            record_hash = hashlib.sha256(row["payload"].encode("utf-8")).hexdigest()
            if record_hash != row["record_sha256"]:
                return VerificationReport(False, expected - start_sequence, "record digest mismatch")
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
                return VerificationReport(False, expected - start_sequence, "entry digest mismatch")
            previous = entry_hash
        return VerificationReport(True, len(rows))
