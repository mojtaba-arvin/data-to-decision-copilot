"""Resource-bounded, read-only DuckDB query execution."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from d2d.core.sql_guard import validate_read_only_sql

_LOGGER = logging.getLogger(__name__)


class QueryExecutionError(RuntimeError):
    """Raised when a query cannot be safely executed."""


class QueryTimeoutError(QueryExecutionError):
    """Raised after the active DuckDB query was interrupted on deadline."""


@dataclass(frozen=True, slots=True)
class ExecutionLimits:
    timeout_seconds: float = 5.0
    max_rows: int = 1_000
    max_bytes: int = 1_048_576
    memory_limit: str = "256MB"

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_rows < 1:
            raise ValueError("max_rows must be at least 1")
        if self.max_bytes < 1:
            raise ValueError("max_bytes must be at least 1")
        if not self.memory_limit:
            raise ValueError("memory_limit must not be empty")


@dataclass(frozen=True, slots=True)
class QueryResult:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    captured_rows: int
    output_bytes: int
    truncated: bool


class DuckDBQueryExecutor:
    """Run validated SELECTs with read-only access and per-query isolation."""

    def __init__(
        self,
        database: str | Path = ":memory:",
        *,
        limits: ExecutionLimits | None = None,
    ) -> None:
        self._database = str(database)
        self._limits = limits or ExecutionLimits()
        self._slot = threading.Lock()

    async def execute(
        self,
        sql: str,
        parameters: list[object] | tuple[object, ...] | None = None,
        *,
        dialect: str = "duckdb",
    ) -> QueryResult:
        validated = validate_read_only_sql(sql, dialect=dialect)
        handle: dict[str, duckdb.DuckDBPyConnection | None] = {"connection": None}
        handle_lock = threading.Lock()
        cancel_requested = threading.Event()
        started_at = time.monotonic()

        def interrupt_this_query() -> None:
            cancel_requested.set()
            with handle_lock:
                connection = handle["connection"]
            if connection is not None:
                try:
                    connection.interrupt()
                except duckdb.Error:
                    _LOGGER.debug("DuckDB interrupt raced with connection shutdown")

        def run() -> QueryResult:
            outcome = "error"
            captured_rows = 0
            with self._slot:
                connection: duckdb.DuckDBPyConnection | None = None
                try:
                    connection = duckdb.connect(
                        self._database,
                        read_only=self._database != ":memory:",
                        config={
                            "enable_external_access": "false",
                            "allow_unsigned_extensions": "false",
                            "autoinstall_known_extensions": "false",
                            "autoload_known_extensions": "false",
                            "threads": "1",
                            "memory_limit": self._limits.memory_limit,
                            "max_temp_directory_size": "0B",
                        },
                    )
                    with handle_lock:
                        handle["connection"] = connection
                    if cancel_requested.is_set():
                        raise QueryTimeoutError("query cancelled before execution")
                    cursor = connection.execute(validated.sql, list(parameters or ()))
                    columns = tuple(item[0] for item in (cursor.description or ()))
                    encoded_columns = json.dumps(
                        columns, ensure_ascii=False, separators=(",", ":")
                    ).encode("utf-8")
                    output_bytes = len(encoded_columns) + 2
                    if output_bytes > self._limits.max_bytes:
                        raise QueryExecutionError("column metadata exceeds max_bytes")
                    rows: list[tuple[Any, ...]] = []
                    truncated = False
                    while len(rows) < self._limits.max_rows:
                        batch = cursor.fetchmany(1)
                        if not batch:
                            break
                        row = tuple(batch[0])
                        encoded = json.dumps(
                            row, ensure_ascii=False, separators=(",", ":"), default=str
                        ).encode("utf-8")
                        extra = len(encoded) + (1 if rows else 0)
                        if output_bytes + extra > self._limits.max_bytes:
                            truncated = True
                            break
                        rows.append(row)
                        output_bytes += extra
                    if not truncated and len(rows) == self._limits.max_rows:
                        truncated = bool(cursor.fetchmany(1))
                    captured_rows = len(rows)
                    outcome = "success"
                    return QueryResult(
                        columns=columns,
                        rows=tuple(rows),
                        captured_rows=captured_rows,
                        output_bytes=output_bytes,
                        truncated=truncated,
                    )
                except duckdb.Error as exc:
                    if cancel_requested.is_set():
                        outcome = "interrupted"
                        raise QueryTimeoutError("query was interrupted") from exc
                    raise QueryExecutionError(f"DuckDB query failed: {exc}") from exc
                finally:
                    with handle_lock:
                        handle["connection"] = None
                    if connection is not None:
                        connection.close()
                    _LOGGER.info(
                        "duckdb_query_finished",
                        extra={
                            "outcome": outcome,
                            "duration_seconds": round(time.monotonic() - started_at, 6),
                            "captured_rows": captured_rows,
                            "database_kind": "memory" if self._database == ":memory:" else "file",
                        },
                    )

        worker = asyncio.create_task(asyncio.to_thread(run))
        try:
            return await asyncio.wait_for(
                asyncio.shield(worker), timeout=self._limits.timeout_seconds
            )
        except TimeoutError as exc:
            interrupt_this_query()
            with contextlib.suppress(QueryExecutionError):
                await worker
            raise QueryTimeoutError(
                f"query exceeded {self._limits.timeout_seconds:g}s and was interrupted"
            ) from exc
        except asyncio.CancelledError:
            interrupt_this_query()
            try:
                await worker
            except QueryExecutionError:
                pass
            raise
