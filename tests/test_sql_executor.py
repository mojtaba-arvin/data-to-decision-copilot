import asyncio
from pathlib import Path

import duckdb
import pytest

from d2d.core.sql_executor import (
    DuckDBQueryExecutor,
    ExecutionLimits,
    QueryTimeoutError,
)
from d2d.core.sql_guard import SQLValidationError, validate_read_only_sql


def test_executes_select_with_bound_parameters_and_caps_rows(tmp_path: Path) -> None:
    database = tmp_path / "facts.duckdb"
    connection = duckdb.connect(str(database))
    connection.execute("CREATE TABLE facts (id INTEGER, value VARCHAR)")
    connection.execute("INSERT INTO facts VALUES (1, 'a'), (2, 'b'), (3, 'c')")
    connection.close()

    async def scenario() -> None:
        executor = DuckDBQueryExecutor(
            database,
            limits=ExecutionLimits(max_rows=2, max_bytes=1024),
        )
        result = await executor.execute(
            "SELECT id, value FROM facts WHERE id > ? ORDER BY id", [0]
        )
        assert result.columns == ("id", "value")
        assert result.rows == ((1, "a"), (2, "b"))
        assert result.captured_rows == 2
        assert result.truncated is True

    asyncio.run(scenario())


def test_enforces_serialized_output_byte_limit() -> None:
    async def scenario() -> None:
        executor = DuckDBQueryExecutor(
            limits=ExecutionLimits(max_rows=10, max_bytes=16)
        )
        result = await executor.execute("SELECT repeat('x', 100) AS value")
        assert result.rows == ()
        assert result.captured_rows == 0
        assert result.truncated is True
        assert result.output_bytes <= 16

    asyncio.run(scenario())


def test_timeout_interrupts_query_and_next_request_isolated() -> None:
    async def scenario() -> None:
        executor = DuckDBQueryExecutor(
            limits=ExecutionLimits(timeout_seconds=0.1, max_rows=5, max_bytes=1024)
        )
        with pytest.raises(QueryTimeoutError):
            await executor.execute("SELECT sum(i) FROM range(100000000000) AS t(i)")
        result = await executor.execute("SELECT 42 AS answer")
        assert result.rows == ((42,),)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT read_parquet('/etc/passwd')",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT unknown_extension_function(1)",
        "SELECT * FROM read_csv_auto('/tmp/input.csv')",
        "SELECT 1; SELECT 2",
        "COPY (SELECT 1) TO '/tmp/out.csv'",
        "ATTACH 'other.db' AS other",
        "SELECT * FROM duckdb_settings()",
    ],
)
def test_rejects_external_access_unknown_functions_and_commands(sql: str) -> None:
    with pytest.raises(SQLValidationError):
        validate_read_only_sql(sql, dialect="duckdb")


def test_rejects_invalid_limits() -> None:
    with pytest.raises(ValueError):
        ExecutionLimits(timeout_seconds=0)
    with pytest.raises(ValueError):
        ExecutionLimits(max_rows=0)
    with pytest.raises(ValueError):
        ExecutionLimits(max_bytes=0)
