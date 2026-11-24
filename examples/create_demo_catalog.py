"""Create a tiny synthetic DuckDB sales catalog for the end-to-end example."""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb


def create_demo_catalog(destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(str(destination))
    try:
        connection.execute("DROP TABLE IF EXISTS sales")
        connection.execute(
            """
            CREATE TABLE sales (
                order_id INTEGER PRIMARY KEY,
                region VARCHAR NOT NULL,
                amount DECIMAL(12, 2) NOT NULL
            )
            """
        )
        connection.executemany(
            "INSERT INTO sales (order_id, region, amount) VALUES (?, ?, ?)",
            [
                (1, "north", 125.50),
                (2, "south", 80.00),
                (3, "north", 210.25),
                (4, "west", 44.75),
            ],
        )
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("examples/analytics.duckdb"))
    args = parser.parse_args()
    create_demo_catalog(args.output)
    print(f"Created synthetic read-only demo catalog: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
