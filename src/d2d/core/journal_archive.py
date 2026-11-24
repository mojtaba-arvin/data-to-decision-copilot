"""Versioned, offline-verifiable manifests for immutable SQLite journal snapshots.

This first archive layer describes verified full-journal snapshots. It does not
rotate a live journal, create non-genesis segments, restore databases, or prune data.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, cast
from urllib.parse import quote

JournalKind = Literal["evidence", "decision_trace"]
GENESIS = "0" * 64
_TABLES = {
    "evidence": ("evidence_journal", "journal_meta"),
    "decision_trace": ("decision_trace_journal", "trace_store_meta"),
}
_MANIFEST_FIELDS = {
    "format_version",
    "journal_kind",
    "database_file",
    "schema_version",
    "records",
    "first_sequence",
    "last_sequence",
    "first_entry_sha256",
    "last_entry_sha256",
    "database_bytes",
    "database_sha256",
    "manifest_sha256",
}


class VerificationResult(Protocol):
    @property
    def valid(self) -> bool: ...

    @property
    def records_checked(self) -> int: ...


@dataclass(frozen=True, slots=True)
class ArchiveManifestReport:
    """Result of validating an archive manifest and its SQLite snapshot."""

    valid: bool
    records_checked: int
    manifest_sha256: str | None = None
    error: str | None = None


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inspect_snapshot(
    database_path: Path,
    *,
    journal_kind: JournalKind,
    schema_version: int,
    verify: Callable[[Path], VerificationResult],
) -> dict[str, Any]:
    if not database_path.is_file():
        raise ValueError("archive database must be an existing regular file")
    report = verify(database_path)
    if not report.valid:
        raise ValueError("archive database journal verification failed")
    if report.records_checked < 0:
        raise ValueError("journal verifier returned an invalid record count")

    table_name, meta_table = _TABLES[journal_kind]
    uri = f"file:{quote(str(database_path))}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        meta = connection.execute(
            f"SELECT value FROM {meta_table} WHERE key = 'schema_version'"
        ).fetchone()
        if meta is None or str(meta[0]) != str(schema_version):
            raise ValueError("archive database schema version mismatch")
        row = connection.execute(
            f"SELECT COUNT(*), MIN(sequence), MAX(sequence) FROM {table_name}"
        ).fetchone()
        first = connection.execute(
            f"SELECT entry_sha256 FROM {table_name} ORDER BY sequence ASC LIMIT 1"
        ).fetchone()
        last = connection.execute(
            f"SELECT entry_sha256 FROM {table_name} ORDER BY sequence DESC LIMIT 1"
        ).fetchone()

    records = int(row[0])
    if records != report.records_checked:
        raise ValueError("journal verifier record count does not match database")
    first_sequence = None if row[1] is None else int(row[1])
    last_sequence = None if row[2] is None else int(row[2])
    if records == 0:
        if first_sequence is not None or last_sequence is not None:
            raise ValueError("empty journal has unexpected sequence bounds")
        first_hash = GENESIS
        last_hash = GENESIS
    else:
        if first_sequence != 1 or last_sequence != records:
            raise ValueError("full-journal snapshot must preserve genesis-to-head sequence")
        first_hash = str(first[0])
        last_hash = str(last[0])

    return {
        "records": records,
        "first_sequence": first_sequence,
        "last_sequence": last_sequence,
        "first_entry_sha256": first_hash,
        "last_entry_sha256": last_hash,
        "database_bytes": database_path.stat().st_size,
        "database_sha256": _file_sha256(database_path),
    }


def create_archive_manifest(
    database_path: str | Path,
    manifest_path: str | Path,
    *,
    journal_kind: JournalKind,
    schema_version: int,
    verify: Callable[[Path], VerificationResult],
) -> ArchiveManifestReport:
    """Create a canonical sidecar manifest for an already-created journal snapshot.

    The SQLite file is never modified. The manifest is published atomically without
    overwriting an existing destination. The caller is responsible for creating the
    snapshot through SQLite's online backup API before calling this function.
    """
    database = Path(database_path).resolve()
    target = Path(manifest_path).resolve()
    if database == target:
        raise ValueError("manifest destination must differ from the database")
    if target.exists():
        raise FileExistsError(f"archive manifest already exists: {target}")
    if journal_kind not in _TABLES:
        raise ValueError("unsupported journal kind")
    if schema_version < 1:
        raise ValueError("schema_version must be positive")

    metadata = _inspect_snapshot(
        database,
        journal_kind=journal_kind,
        schema_version=schema_version,
        verify=verify,
    )
    payload: dict[str, Any] = {
        "format_version": 1,
        "journal_kind": journal_kind,
        "database_file": database.name,
        "schema_version": schema_version,
        **metadata,
    }
    manifest_digest = hashlib.sha256(_canonical(payload)).hexdigest()
    document = {**payload, "manifest_sha256": manifest_digest}
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".manifest-tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_canonical(document) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, target)
        temporary.unlink()
    finally:
        temporary.unlink(missing_ok=True)

    return ArchiveManifestReport(
        valid=True,
        records_checked=int(metadata["records"]),
        manifest_sha256=manifest_digest,
    )


def verify_archive_manifest(
    database_path: str | Path,
    manifest_path: str | Path,
    *,
    journal_kind: JournalKind,
    schema_version: int,
    verify: Callable[[Path], VerificationResult],
) -> ArchiveManifestReport:
    """Verify manifest canonicalization, snapshot bytes, schema, and full hash chain."""
    try:
        database = Path(database_path).resolve()
        manifest_file = Path(manifest_path).resolve()
        raw = manifest_file.read_bytes()
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("archive manifest must be a JSON object")
        document = cast(dict[str, Any], parsed)
        if set(document) != _MANIFEST_FIELDS:
            raise ValueError("archive manifest fields do not match format version 1")
        if raw.rstrip(b"\n") != _canonical(document):
            raise ValueError("archive manifest is not canonical JSON")
        if document["format_version"] != 1:
            raise ValueError("unsupported archive manifest format version")
        if document["journal_kind"] != journal_kind:
            raise ValueError("archive journal kind mismatch")
        if document["database_file"] != database.name:
            raise ValueError("archive database filename mismatch")
        if document["schema_version"] != schema_version:
            raise ValueError("archive schema version mismatch")

        claimed_digest = document["manifest_sha256"]
        payload = {key: value for key, value in document.items() if key != "manifest_sha256"}
        actual_digest = hashlib.sha256(_canonical(payload)).hexdigest()
        if claimed_digest != actual_digest:
            raise ValueError("archive manifest digest mismatch")

        metadata = _inspect_snapshot(
            database,
            journal_kind=journal_kind,
            schema_version=schema_version,
            verify=verify,
        )
        for key, value in metadata.items():
            if document.get(key) != value:
                raise ValueError(f"archive metadata mismatch: {key}")
        return ArchiveManifestReport(
            valid=True,
            records_checked=int(metadata["records"]),
            manifest_sha256=actual_digest,
        )
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError, sqlite3.Error) as exc:
        return ArchiveManifestReport(valid=False, records_checked=0, error=str(exc))


def restore_archive_snapshot(
    database_path: str | Path,
    manifest_path: str | Path,
    destination: str | Path,
    *,
    journal_kind: JournalKind,
    schema_version: int,
    verify: Callable[[Path], VerificationResult],
) -> ArchiveManifestReport:
    """Restore a verified full snapshot to a new path without replacing existing data.

    The destination must retain the snapshot filename because the v1 manifest binds
    that filename. The source is never modified. A staged copy is re-verified before
    an atomic hard-link publication, so an invalid or partial restore is never exposed.
    """
    source = Path(database_path).resolve()
    manifest_file = Path(manifest_path).resolve()
    target = Path(destination).resolve()
    if source == target:
        raise ValueError("restore destination must differ from the archive snapshot")
    if target.name != source.name:
        raise ValueError("restore destination must preserve the archive database filename")
    if not source.is_file():
        raise ValueError("archive database must be an existing regular file")
    if target.exists():
        raise FileExistsError(f"restore destination already exists: {target}")

    source_report = verify_archive_manifest(
        source,
        manifest_file,
        journal_kind=journal_kind,
        schema_version=schema_version,
        verify=verify,
    )
    if not source_report.valid:
        raise ValueError(f"cannot restore an invalid archive: {source_report.error}")

    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".d2d-restore-", dir=target.parent) as staging_dir:
        staged = Path(staging_dir) / source.name
        with source.open("rb") as reader, staged.open("xb") as writer:
            while chunk := reader.read(1024 * 1024):
                writer.write(chunk)
            writer.flush()
            os.fsync(writer.fileno())

        staged_report = verify_archive_manifest(
            staged,
            manifest_file,
            journal_kind=journal_kind,
            schema_version=schema_version,
            verify=verify,
        )
        if not staged_report.valid:
            raise ValueError(f"staged archive verification failed: {staged_report.error}")

        # link() publishes atomically and fails if another writer created target.
        os.link(staged, target)
        directory_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    return staged_report
