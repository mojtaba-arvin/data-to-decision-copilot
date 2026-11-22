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

    def append(self, trace: DecisionTrace) -> int:
        """Append a self-consistent trace atomically and return its sequence."""
        if _trace_digest(trace) != trace.trace_sha256:
            raise ValueError("decision trace digest mismatch")
        payload = _canonical(trace.model_dump(mode="json")).decode("utf-8")
        record_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            last = connection.execute(
                "SELECT sequence, entry_sha256 FROM decision_trace_journal "
                "ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            sequence = 1 if last is None else int(last["sequence"]) + 1
            previous = GENESIS if last is None else str(last["entry_sha256"])
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

    def verify(
        self,
        *,
        evidence_store: EvidenceStore | None = None,
        expected_schema_fingerprints: dict[str, str] | None = None,
    ) -> TraceStoreVerificationReport:
        """Verify the complete persisted chain and optionally each trace's evidence.

        Without an evidence store this verifies persistence integrity and trace
        digests only. Supplying an evidence store additionally verifies evidence
        journal integrity, evidence identity/provenance, and optional schema freshness.
        """
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT sequence, trace_id, payload, record_sha256, "
                "previous_sha256, entry_sha256 FROM decision_trace_journal ORDER BY sequence"
            ).fetchall()

        previous = GENESIS
        evidence_checked = 0
        for expected, row in enumerate(rows, start=1):
            if row["sequence"] != expected:
                return TraceStoreVerificationReport(
                    False, expected - 1, evidence_checked, "non-contiguous sequence"
                )
            if row["previous_sha256"] != previous:
                return TraceStoreVerificationReport(
                    False, expected - 1, evidence_checked, "previous-entry link mismatch"
                )
            try:
                trace = DecisionTrace.model_validate_json(row["payload"])
            except (ValueError, TypeError) as exc:
                return TraceStoreVerificationReport(
                    False, expected - 1, evidence_checked, f"invalid trace: {exc}"
                )
            if trace.trace_id != row["trace_id"]:
                return TraceStoreVerificationReport(
                    False, expected - 1, evidence_checked, "trace ID mismatch"
                )
            if _trace_digest(trace) != trace.trace_sha256:
                return TraceStoreVerificationReport(
                    False, expected - 1, evidence_checked, "decision trace digest mismatch"
                )
            record_hash = hashlib.sha256(row["payload"].encode("utf-8")).hexdigest()
            if record_hash != row["record_sha256"]:
                return TraceStoreVerificationReport(
                    False, expected - 1, evidence_checked, "record digest mismatch"
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
                    False, expected - 1, evidence_checked, "entry digest mismatch"
                )
            if evidence_store is not None:
                report = verify_decision_trace(
                    trace,
                    evidence_store=evidence_store,
                    expected_schema_fingerprints=expected_schema_fingerprints,
                )
                if not report.valid:
                    return TraceStoreVerificationReport(
                        False, expected, evidence_checked + report.evidence_checked,
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
