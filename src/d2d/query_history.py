"""SQLite-backed question history with opt-in pruning; result rows are never stored."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Lock


@dataclass(frozen=True, slots=True)
class QueryHistoryVerificationReport:
    """Structural and SQLite integrity result for a query-history database."""

    valid: bool
    records_checked: int
    error: str | None = None


@dataclass(frozen=True, slots=True)
class QueryHistoryBackupReport:
    """Identity and integrity metadata for an immutable query-history snapshot."""

    path: str
    records: int
    bytes: int
    sha256: str


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


def _verify_history_database(path: Path) -> QueryHistoryVerificationReport:
    try:
        with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                return QueryHistoryVerificationReport(
                    False, 0, "SQLite integrity_check failed"
                )
            columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(query_history)")
            }
            expected = {
                "sequence", "question", "evidence_id", "evidence_sequence",
                "schema_fingerprint", "planner_reference", "created_at",
            }
            if columns != expected:
                return QueryHistoryVerificationReport(
                    False, 0, "query-history schema mismatch"
                )
            count = connection.execute("SELECT COUNT(*) FROM query_history").fetchone()
            return QueryHistoryVerificationReport(True, int(count[0]))
    except (OSError, sqlite3.DatabaseError) as exc:
        return QueryHistoryVerificationReport(False, 0, f"query-history verification failed: {exc}")


class SQLiteQueryHistoryStore:
    """Persist query metadata without copying potentially sensitive result rows."""

    def __init__(self, path: str | Path, *, max_records: int | None = None) -> None:
        if max_records is not None and max_records < 1:
            raise ValueError("max_records must be positive when configured")
        self.path = str(path)
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

    @property
    def max_records(self) -> int | None:
        """Return the configured opt-in automatic retention limit."""
        return self._max_records

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
                if self._max_records is not None:
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
        if limit < 1 or limit > 1_000:
            raise ValueError("history limit must be between 1 and 1000")
        if self._max_records is not None and limit > self._max_records:
            raise ValueError("history limit cannot exceed the configured retention bound")
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

    def verify(self) -> QueryHistoryVerificationReport:
        """Check SQLite integrity and the exact supported query-history schema."""
        with self._lock:
            return _verify_history_database(Path(self.path))

    def backup(self, destination: str | Path) -> QueryHistoryBackupReport:
        """Create a consistent, verified SQLite snapshot without pruning source rows."""
        if self.path == ":memory:":
            raise ValueError("in-memory query history cannot be backed up by path")
        source = Path(self.path).resolve(strict=True)
        target = Path(destination).resolve(strict=False)
        if source == target:
            raise ValueError("backup destination must differ from query history")
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"backup destination already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".backup-tmp", dir=target.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            with self._lock:
                with closing(sqlite3.connect(temporary)) as backup_connection:
                    self._connection.backup(backup_connection)
            report = _verify_history_database(temporary)
            if not report.valid:
                raise ValueError(f"query-history backup verification failed: {report.error}")
            digest = hashlib.sha256()
            with temporary.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            file_descriptor = os.open(temporary, os.O_RDONLY)
            try:
                os.fsync(file_descriptor)
            finally:
                os.close(file_descriptor)
            os.link(temporary, target)
            directory_descriptor = os.open(
                target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            )
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
            return QueryHistoryBackupReport(
                path=str(target),
                records=report.records_checked,
                bytes=target.stat().st_size,
                sha256=digest.hexdigest(),
            )
        finally:
            temporary.unlink(missing_ok=True)

    def close(self) -> None:
        with self._lock:
            self._connection.close()
