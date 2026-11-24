from pathlib import Path

import duckdb
import pytest

from d2d.connectors.base import DataSource, DiscoveryLimits
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


def test_persistent_database_opens_read_only_and_discovers_tables(database_path: Path) -> None:
    with DuckDBConnector(database_path) as source:
        tables = source.list_tables()
        assert [(item.schema, item.name, item.table_type) for item in tables] == [
            ("main", "orders", "BASE TABLE")
        ]
        all_relations = source.list_tables(include_views=True)
        assert {item.name for item in all_relations} == {"orders", "large_orders"}


def test_describe_table_returns_ordered_column_metadata(database_path: Path) -> None:
    with DuckDBConnector(database_path) as source:
        result = source.describe_table("main", "orders")
    assert [column.name for column in result.columns] == ["id", "amount", "note"]
    assert [column.ordinal_position for column in result.columns] == [1, 2, 3]
    assert result.columns[0].nullable is False
    assert result.columns[1].data_type == "DECIMAL(12,2)"


def test_unknown_table_fails_closed(database_path: Path) -> None:
    with DuckDBConnector(database_path) as source, pytest.raises(
        DuckDBConnectorError, match="not found"
    ):
        source.describe_table("main", "missing")


def test_schema_fingerprint_is_stable_and_changes_with_schema(database_path: Path) -> None:
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


def test_connector_requires_open_before_inspection() -> None:
    source = DuckDBConnector()
    with pytest.raises(DuckDBConnectorError, match="not open"):
        source.list_tables()


def test_metadata_queries_do_not_accept_identifier_injection(database_path: Path) -> None:
    with DuckDBConnector(database_path) as source, pytest.raises(
        DuckDBConnectorError, match="not found"
    ):
        source.describe_table("main' OR 1=1 --", "orders")



def test_discovery_limits_validate_positive_values() -> None:
    with pytest.raises(ValueError, match="max_tables"):
        DiscoveryLimits(max_tables=0)
    with pytest.raises(ValueError, match="max_columns_per_table"):
        DiscoveryLimits(max_columns_per_table=0)


def test_table_limit_fails_closed(database_path: Path) -> None:
    with DuckDBConnector(database_path, limits=DiscoveryLimits(max_tables=1)) as source:
        assert len(source.list_tables()) == 1

    connection = duckdb.connect(str(database_path))
    connection.execute("CREATE TABLE second_table (id INTEGER)")
    connection.close()
    with (
        DuckDBConnector(database_path, limits=DiscoveryLimits(max_tables=1)) as source,
        pytest.raises(DuckDBConnectorError, match="max_tables=1"),
    ):
        source.list_tables()


def test_column_limit_fails_closed(database_path: Path) -> None:
    limits = DiscoveryLimits(max_columns_per_table=2)
    with (
        DuckDBConnector(database_path, limits=limits) as source,
        pytest.raises(DuckDBConnectorError, match="max_columns_per_table=2"),
    ):
        source.describe_table("main", "orders")


def test_snapshot_obeys_relation_limit(database_path: Path) -> None:
    connection = duckdb.connect(str(database_path))
    connection.execute("CREATE TABLE second_table (id INTEGER)")
    connection.close()
    with (
        DuckDBConnector(database_path, limits=DiscoveryLimits(max_tables=1)) as source,
        pytest.raises(DuckDBConnectorError, match="max_tables=1"),
    ):
        source.snapshot()


def test_duckdb_connector_satisfies_data_source_protocol(database_path: Path) -> None:
    connector = DuckDBConnector(database_path)
    connector.open()
    source: DataSource = connector
    try:
        assert source.list_tables()
        assert source.describe_table("main", "orders").columns
        assert source.snapshot().fingerprint
    finally:
        connector.close()


def test_connector_rejects_empty_memory_limit() -> None:
    with pytest.raises(ValueError, match="memory_limit"):
        DuckDBConnector(memory_limit="")
