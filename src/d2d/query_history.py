"""SQLite-backed bounded question history; result rows are never stored here."""
from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Lock


@dataclass(frozen=True, slots=True)
class QueryHistoryItem:
    question: str
    evidence_id: str
    evidence_sequence: int
    schema_fingerprint: str
    planner_reference: str
    created_at: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class SQLiteQueryHistoryStore:
    """Persist query metadata without copying potentially sensitive result rows."""

    def __init__(self, path: str | Path, *, max_records: int = 1_000) -> None:
        if max_records < 1:
            raise ValueError("max_records must be positive")
        self._max_records = max_records
        self._lock = Lock()
        self._connection = sqlite3.connect(
            str(path), timeout=5.0, check_same_thread=False, isolation_level=None,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS query_history (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT NOT NULL,
                evidence_id TEXT NOT NULL,
                evidence_sequence INTEGER NOT NULL,
                schema_fingerprint TEXT NOT NULL,
                planner_reference TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )

    def append(self, item: QueryHistoryItem) -> None:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(
                    """
                    INSERT INTO query_history (
                        question, evidence_id, evidence_sequence,
                        schema_fingerprint, planner_reference, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.question, item.evidence_id, item.evidence_sequence,
                        item.schema_fingerprint, item.planner_reference, item.created_at,
                    ),
                )
                self._connection.execute(
                    """
                    DELETE FROM query_history
                    WHERE sequence NOT IN (
                        SELECT sequence FROM query_history
                        ORDER BY sequence DESC LIMIT ?
                    )
                    """,
                    (self._max_records,),
                )
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def list(self, *, limit: int = 100) -> list[QueryHistoryItem]:
        if limit < 1 or limit > self._max_records:
            raise ValueError("history limit must be within the configured record bound")
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT question, evidence_id, evidence_sequence,
                       schema_fingerprint, planner_reference, created_at
                FROM query_history ORDER BY sequence DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [QueryHistoryItem(**dict(row)) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._connection.close()
