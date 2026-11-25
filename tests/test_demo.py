from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from d2d.cli import main
from d2d.demo import create_demo_catalog


def test_demo_catalog_contains_only_expected_synthetic_sales(tmp_path: Path) -> None:
    destination = tmp_path / "nested" / "analytics.duckdb"

    created = create_demo_catalog(destination)

    assert created == destination.resolve()
    connection = duckdb.connect(str(created), read_only=True)
    try:
        assert connection.execute(
            "SELECT order_id, region, amount FROM sales ORDER BY order_id"
        ).fetchall() == [
            (1, "north", 125.50),
            (2, "south", 80.00),
            (3, "north", 210.25),
            (4, "west", 44.75),
        ]
    finally:
        connection.close()


def test_demo_catalog_refuses_to_overwrite_existing_file(tmp_path: Path) -> None:
    destination = tmp_path / "important.duckdb"
    original = b"existing user data"
    destination.write_bytes(original)

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        create_demo_catalog(destination)

    assert destination.read_bytes() == original
    assert list(tmp_path.iterdir()) == [destination]


def test_demo_cli_creates_catalog_without_opening_a_service(tmp_path: Path) -> None:
    destination = tmp_path / "cli-demo.duckdb"

    assert main(["demo", "--output", str(destination)]) == 0

    connection = duckdb.connect(str(destination), read_only=True)
    try:
        assert connection.execute("SELECT count(*) FROM sales").fetchone() == (4,)
    finally:
        connection.close()
