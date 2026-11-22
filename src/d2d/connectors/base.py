"""Shared contracts for bounded, read-only data sources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ColumnInfo:
    """Stable metadata for one source column."""

    name: str
    data_type: str
    nullable: bool
    ordinal_position: int
    default: str | None = None


@dataclass(frozen=True, slots=True)
class TableInfo:
    """A relation discoverable from a data source."""

    catalog: str
    schema: str
    name: str
    table_type: str


@dataclass(frozen=True, slots=True)
class TableSchema:
    """A relation and its ordered column metadata."""

    table: TableInfo
    columns: tuple[ColumnInfo, ...]


@dataclass(frozen=True, slots=True)
class SchemaSnapshot:
    """A bounded, deterministic view of a source schema."""

    tables: tuple[TableSchema, ...]
    fingerprint: str


@dataclass(frozen=True, slots=True)
class DiscoveryLimits:
    """Hard limits for metadata discovery; exceeding a limit fails closed."""

    max_tables: int = 500
    max_columns_per_table: int = 500

    def __post_init__(self) -> None:
        if self.max_tables < 1:
            raise ValueError("max_tables must be at least 1")
        if self.max_columns_per_table < 1:
            raise ValueError("max_columns_per_table must be at least 1")


class DataSource(Protocol):
    """Structural contract implemented by read-only metadata adapters."""

    def list_tables(self, *, include_views: bool = False) -> tuple[TableInfo, ...]:
        """Return discovered relations, subject to configured limits."""
        ...

    def describe_table(self, schema: str, table: str) -> TableSchema:
        """Return ordered metadata for one relation."""
        ...

    def snapshot(self, *, include_views: bool = False) -> SchemaSnapshot:
        """Return a deterministic schema snapshot."""
        ...
