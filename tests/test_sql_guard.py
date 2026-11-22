import pytest

from d2d.core.sql_guard import SQLValidationError, validate_read_only_sql


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id, total FROM orders",
        "WITH monthly AS (SELECT date_trunc('month', created_at) AS month FROM orders) SELECT month FROM monthly",
        "SELECT id FROM a UNION SELECT id FROM b",
        "SELECT id FROM orders WHERE region IN (?, ?) AND amount >= ?",
    ],
)
def test_accepts_read_only_queries(sql: str) -> None:
    result = validate_read_only_sql(sql)
    assert result.sql


@pytest.mark.parametrize(
    "sql",
    [
        "",
        "UPDATE accounts SET balance = 0",
        "DELETE FROM users",
        "DROP TABLE users",
        "SELECT 1; SELECT 2",
        "SELECT * INTO archive FROM users",
        "SELECT read_csv('/etc/passwd')",
        "ATTACH DATABASE 'other.db' AS other",
    ],
)
def test_rejects_unsafe_or_non_query_sql(sql: str) -> None:
    with pytest.raises(SQLValidationError):
        validate_read_only_sql(sql)


def test_normalizes_using_requested_dialect() -> None:
    result = validate_read_only_sql('select "id" from "orders"', dialect="postgres")
    assert result.dialect == "postgres"
    assert '"orders"' in result.sql


@pytest.mark.parametrize(
    "sql",
    [
        "WITH changed AS (DELETE FROM accounts RETURNING *) SELECT * FROM changed",
        "SELECT * FROM read_parquet('/etc/shadow')",
        "SELECT * FROM sqlite_scan('secrets.sqlite', 'users')",
        "SELECT current_setting('secret')",
        "SELECT 1; ATTACH 'remote.db' AS remote",
        "PRAGMA enable_external_access=true",
    ],
)
def test_rejects_adversarial_read_only_bypass_shapes(sql: str) -> None:
    with pytest.raises(SQLValidationError):
        validate_read_only_sql(sql, dialect="duckdb")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM parquet_scan('/etc/shadow')",
        "SELECT * FROM read_csv_auto('/etc/passwd')",
        "SELECT * FROM read_json_auto('/etc/passwd')",
        "SELECT * FROM read_blob('/etc/passwd')",
        "SELECT * FROM read_text('/etc/passwd')",
        "SELECT * FROM glob('/etc/*')",
        "SELECT * FROM query_table('private_table')",
        "SELECT * FROM postgres_scan('host=attacker', 'public', 'users')",
        "SELECT * FROM iceberg_scan('/tmp/untrusted')",
    ],
)
def test_rejects_external_and_dynamic_table_function_scans(sql: str) -> None:
    with pytest.raises(SQLValidationError):
        validate_read_only_sql(sql, dialect="duckdb")
