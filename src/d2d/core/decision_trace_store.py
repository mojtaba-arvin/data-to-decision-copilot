"""SQLite-backed append-only persistence and verification for decision traces."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from d2d.core.contracts import DecisionTrace
from d2d.core.decision_trace import EvidenceStore, verify_decision_trace
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


def _read_checkpoint(connection: sqlite3.Connection) -> tuple[int, str]:
    rows = connection.execute(
        "SELECT key, value FROM trace_store_meta WHERE key IN "
        "('checkpoint_schema_version', 'checkpoint_sequence', 'checkpoint_sha256')"
    ).fetchall()
    metadata = {str(row["key"]): str(row["value"]) for row in rows}
    expected = {"checkpoint_schema_version", "checkpoint_sequence", "checkpoint_sha256"}
    if not metadata:
        return 0, GENESIS
    if set(metadata) != expected or metadata["checkpoint_schema_version"] != "1":
        raise RuntimeError("invalid or unsupported decision trace checkpoint metadata")
    try:
        sequence = int(metadata["checkpoint_sequence"])
        digest = metadata["checkpoint_sha256"]
        if sequence < 0 or len(digest) != 64:
            raise ValueError
        int(digest, 16)
    except ValueError as exc:
        raise RuntimeError("invalid decision trace checkpoint") from exc
    if sequence == 0 and digest != GENESIS:
        raise RuntimeError("genesis checkpoint hash mismatch")
    return sequence, digest


def _trace_payload(trace: DecisionTrace) -> dict[str, Any]:
    return trace.model_dump(mode="json", exclude={"trace_sha256"})


def _trace_digest(trace: DecisionTrace) -> str:
    return hashlib.sha256(_canonical(_trace_payload(trace))).hexdigest()


@dataclass(frozen=True, slots=True)
class TraceStoreVerificationReport:
    """Integrity and optional evidence-verification result for a trace journal."""

    valid: bool
    records_checked: int
    evidence_checked: int = 0
    error: str | None = None


class SQLiteDecisionTraceStore:
    """Durable local trace journal with transactional order and a SHA-256 hash chain.

    The store is append-only under ordinary SQL operations. As with the evidence
    journal, database-owner access can remove triggers or rewrite the whole file;
    independent anchoring/signing is outside this adapter's trust boundary.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS trace_store_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS decision_trace_journal (
                    sequence INTEGER PRIMARY KEY,
                    trace_id TEXT NOT NULL UNIQUE,
                    payload TEXT NOT NULL,
                    record_sha256 TEXT NOT NULL,
                    previous_sha256 TEXT NOT NULL,
                    entry_sha256 TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS decision_trace_no_update
                BEFORE UPDATE ON decision_trace_journal
                BEGIN SELECT RAISE(ABORT, 'decision trace journal is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS decision_trace_no_delete
                BEFORE DELETE ON decision_trace_journal
                BEGIN SELECT RAISE(ABORT, 'decision trace journal is append-only'); END;
                """
            )
            row = connection.execute(
                "SELECT value FROM trace_store_meta WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO trace_store_meta(key, value) VALUES (?, ?)",
                    ("schema_version", str(SCHEMA_VERSION)),
                )
            elif row["value"] != str(SCHEMA_VERSION):
                raise RuntimeError(f"unsupported decision trace schema version: {row['value']}")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def initialize_checkpoint_metadata(self) -> tuple[int, str]:
        """Install additive checkpoint metadata without changing trace records.

        Existing databases remain genesis-anchored until a future verified rotation
        atomically advances this checkpoint. This method never deletes or rewrites rows.
        """
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                report = self.verify()
                if not report.valid:
                    raise ValueError(
                        "cannot initialize checkpoint metadata on an invalid journal: "
                        f"{report.error}"
                    )
                sequence, digest = _read_checkpoint(connection)
                connection.execute(
                    "INSERT OR IGNORE INTO trace_store_meta(key, value) VALUES (?, ?)",
                    ("checkpoint_schema_version", "1"),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO trace_store_meta(key, value) VALUES (?, ?)",
                    ("checkpoint_sequence", str(sequence)),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO trace_store_meta(key, value) VALUES (?, ?)",
                    ("checkpoint_sha256", digest),
                )
                _read_checkpoint(connection)
                connection.commit()
                return sequence, digest
            except Exception:
                connection.rollback()
                raise

    def append(self, trace: DecisionTrace) -> int:
        """Append a self-consistent trace atomically and return its sequence."""
        if _trace_digest(trace) != trace.trace_sha256:
            raise ValueError("decision trace digest mismatch")
        payload = _canonical(trace.model_dump(mode="json")).decode("utf-8")
        record_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            checkpoint_sequence, checkpoint_hash = _read_checkpoint(connection)
            first = connection.execute(
                "SELECT sequence, previous_sha256 FROM decision_trace_journal "
                "ORDER BY sequence ASC LIMIT 1"
            ).fetchone()
            if first is not None and (
                int(first["sequence"]) != checkpoint_sequence + 1
                or str(first["previous_sha256"]) != checkpoint_hash
            ):
                raise RuntimeError(
                    "decision trace checkpoint does not match first stored record"
                )
            last = connection.execute(
                "SELECT sequence, entry_sha256 FROM decision_trace_journal "
                "ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            sequence = (
                checkpoint_sequence + 1 if last is None else int(last["sequence"]) + 1
            )
            previous = checkpoint_hash if last is None else str(last["entry_sha256"])
            entry_hash = hashlib.sha256(
                _canonical(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "sequence": sequence,
                        "trace_id": trace.trace_id,
                        "record_sha256": record_hash,
                        "previous_sha256": previous,
                    }
                )
            ).hexdigest()
            connection.execute(
                """
                INSERT INTO decision_trace_journal
                (sequence, trace_id, payload, record_sha256, previous_sha256, entry_sha256)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (sequence, trace.trace_id, payload, record_hash, previous, entry_hash),
            )
            connection.commit()
        return sequence

    def get(self, trace_id: str) -> DecisionTrace | None:
        """Load a persisted trace by its stable identifier."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT payload FROM decision_trace_journal WHERE trace_id = ?",
                (trace_id,),
            ).fetchone()
        return None if row is None else DecisionTrace.model_validate_json(row["payload"])

    def count(self) -> int:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM decision_trace_journal"
            ).fetchone()
        return int(row["total"])

    def list(
        self, *, limit: int = 100, evidence_store: EvidenceStore | None = None,
    ) -> list[DecisionTrace]:
        """List newest traces only after verifying the complete journal chain."""
        if not 1 <= limit <= 1_000:
            raise ValueError("trace list limit must be between 1 and 1000")
        report = self.verify(evidence_store=evidence_store)
        if not report.valid:
            raise ValueError(f"decision trace journal verification failed: {report.error}")
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT payload FROM decision_trace_journal ORDER BY sequence DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [DecisionTrace.model_validate_json(row["payload"]) for row in rows]

    def backup(self, destination: str | Path) -> JournalBackupReport:
        """Create a verified, atomic snapshot without modifying the live trace journal."""
        report = self.verify()
        if not report.valid:
            raise ValueError(f"cannot back up an invalid decision trace journal: {report.error}")
        return create_verified_backup(
            self.path, destination,
            verify=lambda path: SQLiteDecisionTraceStore(path).verify(),
            table_name="decision_trace_journal",
        )

    def verify(
        self,
        *,
        evidence_store: EvidenceStore | None = None,
        expected_schema_fingerprints: dict[str, str] | None = None,
    ) -> TraceStoreVerificationReport:
        """Verify the active decision-trace journal from its predecessor checkpoint."""
        with closing(self._connect()) as connection:
            checkpoint_sequence, checkpoint_hash = _read_checkpoint(connection)
            count = int(
                connection.execute("SELECT COUNT(*) FROM decision_trace_journal").fetchone()[0]
            )
        if count == 0 and checkpoint_sequence > 0:
            return TraceStoreVerificationReport(True, 0, 0)
        return self.verify_segment(
            start_sequence=checkpoint_sequence + 1,
            previous_sha256=checkpoint_hash,
            evidence_store=evidence_store,
            expected_schema_fingerprints=expected_schema_fingerprints,
        )

    def verify_segment(
        self,
        *,
        start_sequence: int,
        previous_sha256: str,
        evidence_store: EvidenceStore | None = None,
        expected_schema_fingerprints: dict[str, str] | None = None,
    ) -> TraceStoreVerificationReport:
        """Verify a trace segment against an explicit predecessor checkpoint.

        Supplying an evidence store additionally verifies each trace's evidence
        references. The checkpoint itself must be authenticated by the archive
        chain verifier or another trusted anchor.
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
                "SELECT sequence, trace_id, payload, record_sha256, "
                "previous_sha256, entry_sha256 FROM decision_trace_journal ORDER BY sequence"
            ).fetchall()

        if not rows:
            if start_sequence == 1 and previous_sha256 == GENESIS:
                return TraceStoreVerificationReport(True, 0, 0)
            return TraceStoreVerificationReport(False, 0, 0, "journal segment must not be empty")

        previous = previous_sha256
        evidence_checked = 0
        for expected, row in enumerate(rows, start=start_sequence):
            if row["sequence"] != expected:
                return TraceStoreVerificationReport(
                    False, expected - start_sequence, evidence_checked, "non-contiguous sequence"
                )
            if row["previous_sha256"] != previous:
                return TraceStoreVerificationReport(
                    False, expected - start_sequence, evidence_checked, "previous-entry link mismatch"
                )
            try:
                trace = DecisionTrace.model_validate_json(row["payload"])
            except (ValueError, TypeError) as exc:
                return TraceStoreVerificationReport(
                    False, expected - start_sequence, evidence_checked, f"invalid trace: {exc}"
                )
            if trace.trace_id != row["trace_id"]:
                return TraceStoreVerificationReport(
                    False, expected - start_sequence, evidence_checked, "trace ID mismatch"
                )
            if _trace_digest(trace) != trace.trace_sha256:
                return TraceStoreVerificationReport(
                    False, expected - start_sequence, evidence_checked, "decision trace digest mismatch"
                )
            record_hash = hashlib.sha256(row["payload"].encode("utf-8")).hexdigest()
            if record_hash != row["record_sha256"]:
                return TraceStoreVerificationReport(
                    False, expected - start_sequence, evidence_checked, "record digest mismatch"
                )
            entry_hash = hashlib.sha256(
                _canonical(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "sequence": expected,
                        "trace_id": trace.trace_id,
                        "record_sha256": record_hash,
                        "previous_sha256": previous,
                    }
                )
            ).hexdigest()
            if entry_hash != row["entry_sha256"]:
                return TraceStoreVerificationReport(
                    False, expected - start_sequence, evidence_checked, "entry digest mismatch"
                )
            if evidence_store is not None:
                report = verify_decision_trace(
                    trace,
                    evidence_store=evidence_store,
                    expected_schema_fingerprints=expected_schema_fingerprints,
                )
                if not report.valid:
                    return TraceStoreVerificationReport(
                        False, expected - start_sequence + 1,
                        evidence_checked + report.evidence_checked,
                        f"trace {trace.trace_id}: {report.error}",
                    )
                evidence_checked += report.evidence_checked
            previous = entry_hash

        return TraceStoreVerificationReport(True, len(rows), evidence_checked)
    def replay(
        self,
        trace_id: str,
        *,
        evidence_store: EvidenceStore,
        expected_schema_fingerprints: dict[str, str] | None = None,
    ) -> DecisionTrace:
        """Retrieve and verify a historical trace without re-running its SQL or planner."""
        trace = self.get(trace_id)
        if trace is None:
            raise KeyError(f"decision trace not found: {trace_id}")
        report = self.verify(
            evidence_store=evidence_store,
            expected_schema_fingerprints=expected_schema_fingerprints,
        )
        if not report.valid:
            raise ValueError(f"decision trace replay verification failed: {report.error}")
        return trace
