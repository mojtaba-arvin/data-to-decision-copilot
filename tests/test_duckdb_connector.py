from pathlib import Path

import duckdb
import pytest

from d2d.connectors.duckdb import DuckDBConnector, DuckDBConnectorError


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    path = tmp_path / "analytics.duckdb"
    connection = duckdb.connect(str(path))
    connection.execute(
        "CREATE TABLE orders (id INTEGER PRIMARY KEY, amount DECIMAL(12, 2), note VARCHAR)"
    )
    connection.execute("CREATE VIEW large_orders AS SELECT id, amount FROM orders WHERE amount > 100")
    connection.close()
    return path


def test_persistent_database_opens_read_only_and_discovers_tables(database_path):
    with DuckDBConnector(database_path) as source:
        tables = source.list_tables()
        assert [(item.schema, item.name, item.table_type) for item in tables] == [
            ("main", "orders", "BASE TABLE")
        ]
        all_relations = source.list_tables(include_views=True)
        assert {item.name for item in all_relations} == {"orders", "large_orders"}


def test_describe_table_returns_ordered_column_metadata(database_path):
    with DuckDBConnector(database_path) as source:
        result = source.describe_table("main", "orders")
    assert [column.name for column in result.columns] == ["id", "amount", "note"]
    assert [column.ordinal_position for column in result.columns] == [1, 2, 3]
    assert result.columns[0].nullable is False
    assert result.columns[1].data_type == "DECIMAL(12,2)"


def test_unknown_table_fails_closed(database_path):
    with DuckDBConnector(database_path) as source:
        with pytest.raises(DuckDBConnectorError, match="not found"):
            source.describe_table("main", "missing")


def test_schema_fingerprint_is_stable_and_changes_with_schema(database_path):
    with DuckDBConnector(database_path) as source:
        first = source.snapshot()
        second = source.snapshot()
    assert first.fingerprint == second.fingerprint
    assert len(first.fingerprint) == 64

    connection = duckdb.connect(str(database_path))
    connection.execute("ALTER TABLE orders ADD COLUMN region VARCHAR")
    connection.close()
    with DuckDBConnector(database_path) as source:
        changed = source.snapshot()
    assert changed.fingerprint != first.fingerprint


def test_connector_requires_open_before_inspection():
    source = DuckDBConnector()
    with pytest.raises(DuckDBConnectorError, match="not open"):
        source.list_tables()


def test_metadata_queries_do_not_accept_identifier_injection(database_path):
    with DuckDBConnector(database_path) as source:
        with pytest.raises(DuckDBConnectorError, match="not found"):
            source.describe_table("main' OR 1=1 --", "orders")
