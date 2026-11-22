from pathlib import Path

import duckdb
import pytest

from d2d.connectors.base import DataSource, DiscoveryLimits
from d2d.connectors.files import FileDataSource, FileSourceError


@pytest.fixture
def file_root(tmp_path: Path) -> Path:
    (tmp_path / "orders.csv").write_text("id,amount\n1,10.5\n2,20.0\n", encoding="utf-8")
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE TABLE products (sku VARCHAR, quantity INTEGER)")
    connection.execute("INSERT INTO products VALUES ('A-1', 4), ('B-2', 8)")
    target = (tmp_path / "products.parquet").as_posix().replace("'", "''")
    connection.execute(f"COPY products TO '{target}' (FORMAT PARQUET)")
    connection.close()
    return tmp_path

def test_csv_and_parquet_discovery_is_metadata_only(file_root: Path) -> None:
    with FileDataSource(file_root, ["orders.csv", "products.parquet"]) as source:
        typed_source: DataSource = source
        assert typed_source.list_tables()
        relations = source.list_tables()
        assert [item.table_type for item in relations] == ["CSV", "PARQUET"]
        csv_schema = source.describe_table("main", "source_0001")
        parquet_schema = source.describe_table("main", "source_0002")
        assert [column.name for column in csv_schema.columns] == ["id", "amount"]
        assert [column.name for column in parquet_schema.columns] == ["sku", "quantity"]
        assert source.snapshot().fingerprint == source.snapshot().fingerprint

def test_requires_explicit_file_list(file_root: Path) -> None:
    with pytest.raises(FileSourceError, match="explicit file"):
        FileDataSource(file_root, []).open()

@pytest.mark.parametrize("path", ["../outside.csv", "/etc/passwd"])
def test_rejects_traversal_and_absolute_paths(file_root: Path, path: str) -> None:
    with pytest.raises(FileSourceError, match="relative paths"):
        FileDataSource(file_root, [path]).open()

def test_rejects_symlink(file_root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.csv"
    outside.write_text("secret\nvalue\n", encoding="utf-8")
    (file_root / "linked.csv").symlink_to(outside)
    with pytest.raises(FileSourceError, match="symlinks"):
        FileDataSource(file_root, ["linked.csv"]).open()

def test_rejects_oversized_file(file_root: Path) -> None:
    with pytest.raises(FileSourceError, match="max_file_bytes"):
        FileDataSource(file_root, ["orders.csv"], max_file_bytes=2).open()

def test_rejects_unsupported_extension(file_root: Path) -> None:
    (file_root / "notes.txt").write_text("not data", encoding="utf-8")
    with pytest.raises(FileSourceError, match="only .csv and .parquet"):
        FileDataSource(file_root, ["notes.txt"]).open()

def test_rejects_excessive_file_count(file_root: Path) -> None:
    with pytest.raises(FileSourceError, match="max_tables=1"):
        FileDataSource(file_root, ["orders.csv", "products.parquet"],
                       limits=DiscoveryLimits(max_tables=1)).open()

def test_rejects_excessive_columns(file_root: Path) -> None:
    with pytest.raises(FileSourceError, match="max_columns_per_table=1"):
        FileDataSource(file_root, ["orders.csv"],
                       limits=DiscoveryLimits(max_columns_per_table=1)).open()

def test_csv_inference_sample_must_be_positive(file_root: Path) -> None:
    with pytest.raises(ValueError, match="csv_sample_size"):
        FileDataSource(file_root, ["orders.csv"], csv_sample_size=0)


def test_file_catalog_materializes_sources_and_enforces_total_size(file_root: Path, tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.duckdb"
    with FileDataSource(
        file_root, ["orders.csv", "products.parquet"], catalog_path=catalog
    ) as source:
        assert source.catalog_path == str(catalog)
        assert source.list_tables()
        assert source.list_tables()[0].name == "source_0001"
    with pytest.raises(FileSourceError, match="max_total_bytes"):
        FileDataSource(
            file_root, ["orders.csv", "products.parquet"], max_total_bytes=1
        ).open()
