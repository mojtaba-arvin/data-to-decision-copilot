"""Bounded, read-only CSV and Parquet file discovery."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path, PurePath
from typing import Self

import duckdb

from d2d.connectors.base import (
    ColumnInfo,
    DiscoveryLimits,
    SchemaSnapshot,
    TableInfo,
    TableSchema,
)


class FileSourceError(RuntimeError):
    """Raised when a file source violates policy or cannot be inspected."""

class FileDataSource:
    """Metadata adapter for explicitly listed CSV/Parquet files under one root.

    Paths are relative, never glob-expanded, and every path component is
    checked for symlinks before opening. File size is checked before DuckDB
    reads a file. CSV schema inference is bounded by csv_sample_size.
    The adapter owns an isolated in-memory DuckDB connection and exposes no
    arbitrary SQL execution method.
    """
    def __init__(self, root: str | Path, files: tuple[str | Path, ...] | list[str | Path], *,
                 max_file_bytes: int = 64 * 1024 * 1024,
                 max_total_bytes: int = 256 * 1024 * 1024,
                 csv_sample_size: int = 20_480,
                 catalog_path: str | Path = ":memory:",
                 memory_limit: str = "256MB",
                 limits: DiscoveryLimits | None = None) -> None:
        if max_file_bytes < 1:
            raise ValueError("max_file_bytes must be at least 1")
        if max_total_bytes < 1:
            raise ValueError("max_total_bytes must be at least 1")
        if not str(catalog_path):
            raise ValueError("catalog_path must not be empty")
        if not memory_limit:
            raise ValueError("memory_limit must not be empty")
        if csv_sample_size < 1:
            raise ValueError("csv_sample_size must be at least 1")
        self._root_input = Path(root)
        self._files = tuple(Path(item) for item in files)
        self._max_file_bytes = max_file_bytes
        self._max_total_bytes = max_total_bytes
        self._csv_sample_size = csv_sample_size
        self._catalog_path = str(catalog_path)
        self._memory_limit = memory_limit
        self._limits = limits or DiscoveryLimits()
        self._connection: duckdb.DuckDBPyConnection | None = None
        self._tables: tuple[TableInfo, ...] = ()
        self._snapshot_dir: tempfile.TemporaryDirectory[str] | None = None

    def __enter__(self) -> Self:
        return self.open()

    def __exit__(self, *_: object) -> None:
        self.close()

    def open(self) -> Self:
        if self._connection is not None:
            return self
        snapshot_dir: tempfile.TemporaryDirectory[str] | None = None
        try:
            if self._root_input.is_symlink():
                raise FileSourceError("source root must not be a symlink")
            root = self._root_input.resolve(strict=True)
            if not root.is_dir():
                raise FileSourceError("source root must be a directory")
            if not self._files:
                raise FileSourceError("at least one explicit file is required")
            if len(self._files) > self._limits.max_tables:
                raise FileSourceError(f"file count exceeds max_tables={self._limits.max_tables}")
            validated_files = [self._validate_file(root, item) for item in self._files]
            total_bytes = sum(path.stat().st_size for path in validated_files)
            if total_bytes > self._max_total_bytes:
                raise FileSourceError(
                    f"source files exceed max_total_bytes={self._max_total_bytes}"
                )
            if self._catalog_path != ":memory:":
                catalog = Path(self._catalog_path)
                if catalog.is_symlink():
                    raise FileSourceError("catalog database must not be a symlink")
                if catalog.exists() and not catalog.is_file():
                    raise FileSourceError("catalog database must be a regular file")
                if catalog.resolve() in validated_files:
                    raise FileSourceError("catalog database must not overwrite a source file")

            # Snapshot through no-follow descriptors before DuckDB opens paths.
            snapshot_dir = tempfile.TemporaryDirectory(prefix="d2d-file-snapshot-")
            snapshot_root = Path(snapshot_dir.name)
            snapshot_files: list[Path] = []
            for index, relative in enumerate(self._files, start=1):
                target = snapshot_root / f"source_{index:04d}{relative.suffix.lower()}"
                self._copy_snapshot(root, relative, target)
                snapshot_files.append(target)
            snapshot_bytes = sum(path.stat().st_size for path in snapshot_files)
            if snapshot_bytes > self._max_total_bytes:
                raise FileSourceError(
                    f"source files exceed max_total_bytes={self._max_total_bytes}"
                )

            connection = duckdb.connect(self._catalog_path)
            catalog_name = (
                "memory" if self._catalog_path == ":memory:"
                else Path(self._catalog_path).stem
            )
            tables: list[TableInfo] = []
            try:
                for index, path in enumerate(snapshot_files, start=1):
                    name = f"source_{index:04d}"
                    if path.suffix.lower() == ".csv":
                        relation = connection.read_csv(str(path), sample_size=self._csv_sample_size)
                        table_type = "CSV"
                    else:
                        relation = connection.read_parquet(str(path))
                        table_type = "PARQUET"
                    if len(relation.columns) > self._limits.max_columns_per_table:
                        raise FileSourceError("file schema exceeds "
                                              f"max_columns_per_table={self._limits.max_columns_per_table}")
                    relation.create(name)
                    tables.append(TableInfo(catalog=catalog_name, schema="main",
                                            name=name, table_type=table_type))
            except Exception:
                connection.close()
                raise
            if self._catalog_path != ":memory:":
                connection.close()
                connection = duckdb.connect(
                    self._catalog_path,
                    read_only=True,
                    config={
                        "enable_external_access": "false",
                        "allow_unsigned_extensions": "false",
                        "autoinstall_known_extensions": "false",
                        "autoload_known_extensions": "false",
                        "threads": "1",
                        "memory_limit": self._memory_limit,
                        "max_temp_directory_size": "0B",
                    },
                )
            self._connection = connection
            self._tables = tuple(tables)
            self._snapshot_dir = snapshot_dir
            return self
        except FileSourceError:
            if snapshot_dir is not None:
                snapshot_dir.cleanup()
            raise
        except (OSError, duckdb.Error, ValueError) as exc:
            if snapshot_dir is not None:
                snapshot_dir.cleanup()
            raise FileSourceError(f"could not open file source: {exc}") from exc

    @property
    def catalog_path(self) -> str:
        """DuckDB catalog used by a matching DuckDBQueryExecutor."""
        return self._catalog_path

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
            self._tables = ()
        if self._snapshot_dir is not None:
            self._snapshot_dir.cleanup()
            self._snapshot_dir = None

    def list_tables(self, *, include_views: bool = False) -> tuple[TableInfo, ...]:
        del include_views
        self._require_connection()
        return self._tables

    def describe_table(self, schema: str, table: str) -> TableSchema:
        connection = self._require_connection()
        matches = [item for item in self._tables if item.schema == schema and item.name == table]
        if len(matches) != 1:
            raise FileSourceError(f"file relation not found: {schema}.{table}")
        rows = connection.execute(
            """SELECT column_name, data_type, is_nullable, ordinal_position, column_default
               FROM information_schema.columns WHERE table_schema = ? AND table_name = ?
               ORDER BY ordinal_position""", [schema, table]).fetchall()
        if len(rows) > self._limits.max_columns_per_table:
            raise FileSourceError("file schema exceeds "
                                  f"max_columns_per_table={self._limits.max_columns_per_table}")
        columns = tuple(ColumnInfo(name=str(row[0]), data_type=str(row[1]),
                                   nullable=str(row[2]).upper() == "YES",
                                   ordinal_position=int(row[3]),
                                   default=None if row[4] is None else str(row[4]))
                        for row in rows)
        return TableSchema(table=matches[0], columns=columns)

    def snapshot(self, *, include_views: bool = False) -> SchemaSnapshot:
        tables = tuple(self.describe_table(item.schema, item.name)
                       for item in self.list_tables(include_views=include_views))
        canonical = [{"catalog": item.table.catalog, "schema": item.table.schema,
                      "name": item.table.name, "type": item.table.table_type,
                      "columns": [{"name": col.name, "type": col.data_type,
                                   "nullable": col.nullable, "ordinal": col.ordinal_position,
                                   "default": col.default} for col in item.columns]}
                     for item in tables]
        encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return SchemaSnapshot(tables=tables,
                              fingerprint=hashlib.sha256(encoded.encode("utf-8")).hexdigest())

    def _validate_file(self, root: Path, relative: Path) -> Path:
        pure = PurePath(relative)
        if relative.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
            raise FileSourceError("file paths must be non-empty relative paths without traversal")
        current = root
        for part in pure.parts:
            current = current / part
            if current.is_symlink():
                raise FileSourceError("symlinks are not allowed in file source paths")
        try:
            resolved = current.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError) as exc:
            raise FileSourceError("file path is missing or escapes the configured root") from exc
        if not resolved.is_file():
            raise FileSourceError("source path must be a regular file")
        if resolved.suffix.lower() not in {".csv", ".parquet"}:
            raise FileSourceError("only .csv and .parquet files are supported")
        size = resolved.stat().st_size
        if size == 0:
            raise FileSourceError("source file must not be empty")
        if size > self._max_file_bytes:
            raise FileSourceError(f"file exceeds max_file_bytes={self._max_file_bytes}")
        return resolved

    def _copy_snapshot(self, root: Path, relative: Path, destination: Path) -> None:
        """Copy one validated input through no-follow descriptors into private storage."""
        nofollow = getattr(os, "O_NOFOLLOW", None)
        directory = getattr(os, "O_DIRECTORY", None)
        if nofollow is None or directory is None:
            raise FileSourceError("secure file snapshots require O_NOFOLLOW and O_DIRECTORY")
        directory_flags = os.O_RDONLY | nofollow | directory
        file_flags = os.O_RDONLY | nofollow
        root_fd = os.open(root, directory_flags)
        current_fd = root_fd
        file_fd: int | None = None
        try:
            parts = PurePath(relative).parts
            for part in parts[:-1]:
                next_fd = os.open(part, directory_flags, dir_fd=current_fd)
                if current_fd != root_fd:
                    os.close(current_fd)
                current_fd = next_fd
            file_fd = os.open(parts[-1], file_flags, dir_fd=current_fd)
            before = os.fstat(file_fd)
            if not stat.S_ISREG(before.st_mode):
                raise FileSourceError("source path must be a regular file")
            if before.st_size > self._max_file_bytes:
                raise FileSourceError(
                    f"file exceeds max_file_bytes={self._max_file_bytes}"
                )
            copied = 0
            with os.fdopen(file_fd, "rb") as source:
                file_fd = None
                with destination.open("xb") as target:
                    while chunk := source.read(1024 * 1024):
                        copied += len(chunk)
                        if copied > self._max_file_bytes:
                            raise FileSourceError(
                                f"file exceeds max_file_bytes={self._max_file_bytes}"
                            )
                        target.write(chunk)
                after = os.fstat(source.fileno())
            before_identity = (
                before.st_dev, before.st_ino, before.st_size,
                before.st_mtime_ns, before.st_ctime_ns,
            )
            after_identity = (
                after.st_dev, after.st_ino, after.st_size,
                after.st_mtime_ns, after.st_ctime_ns,
            )
            if copied != before.st_size or before_identity != after_identity:
                raise FileSourceError("source file changed while creating its snapshot")
        finally:
            if file_fd is not None:
                os.close(file_fd)
            if current_fd != root_fd:
                os.close(current_fd)
            os.close(root_fd)

    def _require_connection(self) -> duckdb.DuckDBPyConnection:
        if self._connection is None:
            raise FileSourceError("file source is not open; use open() or a context manager")
        return self._connection