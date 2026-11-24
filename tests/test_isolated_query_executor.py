import asyncio
from pathlib import Path

import duckdb
import pytest

from d2d.core.isolated_query_executor import (
    IsolatedDuckDBQueryExecutor,
    WorkerLimits,
)
from d2d.core.sql_executor import ExecutionLimits, QueryExecutionError


def test_isolated_worker_executes_read_only_query(tmp_path: Path) -> None:
    database = tmp_path / "isolated.duckdb"
    connection = duckdb.connect(str(database))
    connection.execute("CREATE TABLE facts (value INTEGER)")
    connection.execute("INSERT INTO facts VALUES (7), (8)")
    connection.close()

    async def scenario() -> None:
        executor = IsolatedDuckDBQueryExecutor(
            database,
            limits=ExecutionLimits(timeout_seconds=5, max_rows=10, max_bytes=1024),
        )
        result = await executor.execute(
            "SELECT value FROM facts WHERE value > ? ORDER BY value", [6]
        )
        assert result.columns == ("value",)
        assert result.rows == ((7,), (8,))
        assert result.captured_rows == 2

    asyncio.run(scenario())


def test_isolated_worker_rejects_invalid_sql(tmp_path: Path) -> None:
    database = tmp_path / "isolated.duckdb"

    async def scenario() -> None:
        executor = IsolatedDuckDBQueryExecutor(database)
        with pytest.raises(QueryExecutionError, match="SQLValidationError"):
            await executor.execute("DELETE FROM facts")

    asyncio.run(scenario())


def test_isolated_worker_enforces_parent_wall_clock_deadline(tmp_path: Path) -> None:
    async def scenario() -> None:
        executor = IsolatedDuckDBQueryExecutor(
            tmp_path / "isolated.duckdb",
            limits=ExecutionLimits(timeout_seconds=0.01, max_rows=1, max_bytes=128),
        )
        with pytest.raises(QueryExecutionError, match="wall-clock limit"):
            await executor.execute("SELECT sum(i) FROM range(100000000000) AS t(i)")

    asyncio.run(scenario())


def test_worker_limits_reject_unsafe_values() -> None:
    with pytest.raises(ValueError):
        WorkerLimits(process_memory_bytes=1)
    with pytest.raises(ValueError):
        WorkerLimits(max_request_bytes=1)
    with pytest.raises(ValueError):
        WorkerLimits(startup_grace_seconds=-1)


def test_isolated_worker_rejects_oversized_request(tmp_path: Path) -> None:
    async def scenario() -> None:
        executor = IsolatedDuckDBQueryExecutor(
            tmp_path / "isolated.duckdb",
            worker_limits=WorkerLimits(max_request_bytes=256),
        )
        with pytest.raises(QueryExecutionError, match="request exceeds"):
            await executor.execute("SELECT '" + ("x" * 1024) + "'")

    asyncio.run(scenario())
