"""Read-only DuckDB catalog discovery for local analytical data."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Self

import duckdb


class DuckDBConnectorError(RuntimeError):
    """Raised when a DuckDB source cannot be opened or inspected."""


@dataclass(frozen=True, slots=True)
class ColumnInfo:
    name: str
    data_type: str
    nullable: bool
    ordinal_position: int
    default: str | None = None


@dataclass(frozen=True, slots=True)
class TableInfo:
    catalog: str
    schema: str
    name: str
    table_type: str


@dataclass(frozen=True, slots=True)
class TableSchema:
    table: TableInfo
    columns: tuple[ColumnInfo, ...]


@dataclass(frozen=True, slots=True)
class SchemaSnapshot:
    tables: tuple[TableSchema, ...]
    fingerprint: str


class DuckDBConnector:
    """Expose catalog metadata without exposing a write-capable query API.

    Persistent database files are opened with DuckDB's read-only flag. The
    in-memory mode is isolated to this process and is intended for tests and
    ephemeral imports; this class still exposes metadata operations only.
    """

    def __init__(self, database: str | Path = ":memory:") -> None:
        self._database = str(database)
        self._connection: duckdb.DuckDBPyConnection | None = None

    def __enter__(self) -> Self:
        return self.open()

    def __exit__(self, *_: object) -> None:
        self.close()

    def open(self) -> Self:
        if self._connection is not None:
            return self
        in_memory = self._database == ":memory:"
        try:
            self._connection = duckdb.connect(
                self._database,
                read_only=not in_memory,
            )
        except (duckdb.Error, OSError) as exc:
            raise DuckDBConnectorError(f"could not open DuckDB source: {exc}") from exc
        return self

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def list_tables(self, *, include_views: bool = False) -> tuple[TableInfo, ...]:
        connection = self._require_connection()
        table_filter = "TRUE" if include_views else "table_type = 'BASE TABLE'"
        rows = connection.execute(
            f"""SELECT table_catalog, table_schema, table_name, table_type
                FROM information_schema.tables
                WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
                  AND {table_filter}
                ORDER BY table_catalog, table_schema, table_name"""
        ).fetchall()
        return tuple(
            TableInfo(
                catalog=str(row[0]),
                schema=str(row[1]),
                name=str(row[2]),
                table_type=str(row[3]),
            )
            for row in rows
        )

    def describe_table(self, schema: str, table: str) -> TableSchema:
        connection = self._require_connection()
        table_rows = connection.execute(
            """SELECT table_catalog, table_schema, table_name, table_type
               FROM information_schema.tables
               WHERE table_schema = ? AND table_name = ?
                 AND table_schema NOT IN ('information_schema', 'pg_catalog')""",
            [schema, table],
        ).fetchall()
        if not table_rows:
            raise DuckDBConnectorError(f"table or view not found: {schema}.{table}")
        if len(table_rows) != 1:
            raise DuckDBConnectorError(f"ambiguous table reference: {schema}.{table}")

        row = table_rows[0]
        table_info = TableInfo(
            catalog=str(row[0]),
            schema=str(row[1]),
            name=str(row[2]),
            table_type=str(row[3]),
        )
        column_rows = connection.execute(
            """SELECT column_name, data_type, is_nullable, ordinal_position, column_default
               FROM information_schema.columns
               WHERE table_schema = ? AND table_name = ?
               ORDER BY ordinal_position""",
            [schema, table],
        ).fetchall()
        columns = tuple(
            ColumnInfo(
                name=str(column[0]),
                data_type=str(column[1]),
                nullable=str(column[2]).upper() == "YES",
                ordinal_position=int(column[3]),
                default=None if column[4] is None else str(column[4]),
            )
            for column in column_rows
        )
        return TableSchema(table=table_info, columns=columns)

    def snapshot(self, *, include_views: bool = False) -> SchemaSnapshot:
        tables = tuple(
            self.describe_table(item.schema, item.name)
            for item in self.list_tables(include_views=include_views)
        )
        canonical = [
            {
                "catalog": item.table.catalog,
                "schema": item.table.schema,
                "name": item.table.name,
                "type": item.table.table_type,
                "columns": [
                    {
                        "name": column.name,
                        "type": column.data_type,
                        "nullable": column.nullable,
                        "ordinal": column.ordinal_position,
                        "default": column.default,
                    }
                    for column in item.columns
                ],
            }
            for item in tables
        ]
        encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        return SchemaSnapshot(tables=tables, fingerprint=fingerprint)

    def _require_connection(self) -> duckdb.DuckDBPyConnection:
        if self._connection is None:
            raise DuckDBConnectorError("connector is not open; use open() or a context manager")
        return self._connection
