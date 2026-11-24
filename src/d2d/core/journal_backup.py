"""Consistent, verified SQLite journal backups with atomic no-overwrite publication."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class VerificationResult(Protocol):
    valid: bool
    records_checked: int


@dataclass(frozen=True, slots=True)
class JournalBackupReport:
    """Identity and integrity metadata for a completed SQLite journal snapshot."""

    path: str
    records: int
    bytes: int
    sha256: str
    head_sha256: str


def create_verified_backup(
    source_path: str,
    destination: str | Path,
    *,
    verify: Callable[[Path], VerificationResult],
    table_name: str,
) -> JournalBackupReport:
    """Back up a verified on-disk journal using SQLite's consistent backup API.

    The destination is published atomically and is never overwritten. The backup
    is re-verified before publication; no source records are deleted or compacted.
    """
    if source_path == ":memory:":
        raise ValueError("in-memory journals cannot be backed up by path")
    source = Path(source_path).resolve()
    target = Path(destination).resolve()
    if source == target:
        raise ValueError("backup destination must differ from the source journal")
    if target.exists():
        raise FileExistsError(f"backup destination already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".backup-tmp", dir=target.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with closing(sqlite3.connect(source)) as source_connection:
            with closing(sqlite3.connect(temporary)) as backup_connection:
                source_connection.backup(backup_connection)
                backup_connection.commit()

        report = verify(temporary)
        if not report.valid:
            raise ValueError(f"journal backup verification failed: {getattr(report, 'error', None)}")

        with closing(sqlite3.connect(temporary)) as connection:
            count_row = connection.execute(
                f"SELECT COUNT(*) FROM {table_name}"
            ).fetchone()
            head_row = connection.execute(
                f"SELECT entry_sha256 FROM {table_name} ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
        records = int(count_row[0])
        head_sha256 = "0" * 64 if head_row is None else str(head_row[0])
        digest = hashlib.sha256()
        with temporary.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)

        # A hard-link publication is atomic and fails rather than replacing a backup.
        os.link(temporary, target)
        temporary.unlink()
        return JournalBackupReport(
            path=str(target),
            records=records,
            bytes=target.stat().st_size,
            sha256=digest.hexdigest(),
            head_sha256=head_sha256,
        )
    finally:
        temporary.unlink(missing_ok=True)
