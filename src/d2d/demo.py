"""Create a synthetic sales catalog with no-overwrite atomic publication."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import duckdb


def create_demo_catalog(destination: str | Path) -> Path:
    """Create a four-row synthetic sales catalog without replacing existing data.

    The database is built in a temporary directory on the destination filesystem,
    closed and synced, then published with a hard link. The link operation is
    atomic and fails if the destination already exists, including a dangling
    symlink. A failed build never leaves a partial destination behind.
    """
    requested = Path(destination).expanduser()
    if not requested.name:
        raise ValueError("destination must name a catalog file")
    parent = requested.parent.resolve()
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / requested.name
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"refusing to overwrite existing catalog: {target}")

    with tempfile.TemporaryDirectory(
        prefix=f".{target.name}.", dir=parent
    ) as temporary_directory:
        database = Path(temporary_directory) / "analytics.duckdb"
        connection = duckdb.connect(str(database))
        try:
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

        descriptor = os.open(database, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

        # Hard-link publication is atomic and never overwrites an existing path.
        # The temporary directory is on the same filesystem as the destination.
        os.link(database, target)

        if hasattr(os, "O_DIRECTORY"):
            directory_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)

    return target
