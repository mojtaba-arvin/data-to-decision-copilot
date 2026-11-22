import pytest

from d2d.core.sql_guard import SQLValidationError, validate_read_only_sql


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id, total FROM orders",
        "WITH monthly AS (SELECT date_trunc('month', created_at) AS month FROM orders) SELECT month FROM monthly",
        "SELECT id FROM a UNION SELECT id FROM b",
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
