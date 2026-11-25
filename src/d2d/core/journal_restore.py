"""Operator-facing, verified restore of a paired Evidence and DecisionTrace archive."""

from __future__ import annotations

import ctypes
import errno
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from d2d.core.decision_trace_store import SCHEMA_VERSION as TRACE_SCHEMA_VERSION
from d2d.core.decision_trace_store import SQLiteDecisionTraceStore
from d2d.core.evidence_store import SCHEMA_VERSION as EVIDENCE_SCHEMA_VERSION
from d2d.core.evidence_store import SQLiteEvidenceStore
from d2d.core.journal_chain import (
    ArchiveChainRestoreReport,
    restore_archive_chain,
)


@dataclass(frozen=True, slots=True)
class PairedJournalRestoreReport:
    """Verified result of publishing both journals as one fresh directory."""

    destination: str
    evidence_segments_checked: int
    evidence_records_restored: int
    evidence_head_sha256: str
    trace_segments_checked: int
    trace_records_restored: int
    trace_head_sha256: str
    traces_evidence_checked: int


def _discover_segments(
    archive_directory: str | Path,
    *,
    journal_kind: str,
    schema_version: int,
) -> list[tuple[Path, Path]]:
    supplied_root = Path(archive_directory)
    if supplied_root.is_symlink():
        raise ValueError(f"{journal_kind} archive directory must not be a symlink")
    root = supplied_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"{journal_kind} archive path must be a directory")

    manifests = sorted(root.glob("*.manifest.json"))
    if not manifests:
        raise ValueError(f"{journal_kind} archive contains no segment manifests")

    discovered: list[tuple[int, Path, Path]] = []
    for manifest_path in manifests:
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise ValueError(f"invalid {journal_kind} manifest file: {manifest_path}")
        document = json.loads(manifest_path.read_bytes())
        if not isinstance(document, dict):
            raise ValueError(f"invalid {journal_kind} manifest: expected an object")
        if document.get("journal_kind") != journal_kind:
            raise ValueError(f"archive contains a non-{journal_kind} manifest")
        if document.get("schema_version") != schema_version:
            raise ValueError(f"unsupported {journal_kind} schema version")
        first_sequence = document.get("first_sequence")
        if (
            not isinstance(first_sequence, int)
            or isinstance(first_sequence, bool)
            or first_sequence < 1
        ):
            raise ValueError(f"invalid first_sequence in {manifest_path.name}")

        database_name = document.get("database_file")
        if (
            not isinstance(database_name, str)
            or not database_name
            or database_name in {".", ".."}
            or Path(database_name).name != database_name
            or "/" in database_name
            or "\\" in database_name
        ):
            raise ValueError(f"unsafe database_file in {manifest_path.name}")
        database_path = root / database_name
        if database_path.is_symlink() or not database_path.is_file():
            raise ValueError(f"missing or unsafe database artifact: {database_path}")
        database = database_path.resolve(strict=True)
        manifest = manifest_path.resolve(strict=True)
        if database.parent != root or manifest.parent != root:
            raise ValueError("archive artifacts must remain inside their archive directory")
        discovered.append((first_sequence, database, manifest))

    discovered.sort(key=lambda item: item[0])
    return [(database, manifest) for _, database, manifest in discovered]


def _verify_evidence_segment(path: Path, start: int, previous: str):
    store = SQLiteEvidenceStore(path, trusted_checkpoint=(start - 1, previous))
    return store.verify_segment(start_sequence=start, previous_sha256=previous)


def _verify_trace_segment(path: Path, start: int, previous: str):
    store = SQLiteDecisionTraceStore(path, trusted_checkpoint=(start - 1, previous))
    return store.verify_segment(start_sequence=start, previous_sha256=previous)


def _rename_directory_no_replace(source: Path, destination: Path) -> None:
    """Atomically publish a directory without replacing any existing path."""
    if os.name == "nt":
        os.rename(source, destination)
        return

    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "linux":
        rename = getattr(libc, "renameat2", None)
        if rename is None:
            raise OSError(errno.ENOTSUP, "atomic no-replace directory rename is unavailable")
        rename.argtypes = [
            ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint
        ]
        rename.restype = ctypes.c_int
        result = rename(
            -100, os.fsencode(source), -100, os.fsencode(destination), 1
        )
    elif sys.platform == "darwin":
        rename = getattr(libc, "renamex_np", None)
        if rename is None:
            raise OSError(errno.ENOTSUP, "atomic no-replace directory rename is unavailable")
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        result = rename(os.fsencode(source), os.fsencode(destination), 0x00000004)
    else:
        raise OSError(errno.ENOTSUP, "atomic no-replace directory rename is unsupported")

    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


def _fsync_file(path: Path) -> None:
    with path.open("rb") as stream:
        os.fsync(stream.fileno())


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def restore_paired_journals(
    evidence_archive: str | Path,
    trace_archive: str | Path,
    destination: str | Path,
) -> PairedJournalRestoreReport:
    """Restore and verify both journal chains, then publish the directory atomically.

    The destination must not exist. Both databases are reconstructed in a
    private staging directory; the trace journal is checked against the restored
    evidence journal before a single no-replace directory rename publishes them.
    """
    evidence_root = Path(evidence_archive).resolve(strict=True)
    trace_root = Path(trace_archive).resolve(strict=True)
    supplied_target = Path(destination)
    if supplied_target.is_symlink():
        raise FileExistsError(f"restore destination already exists: {supplied_target}")
    target = supplied_target.resolve(strict=False)
    if target.name in {"", ".", ".."}:
        raise ValueError("restore destination must name a new directory")
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"restore destination already exists: {target}")
    for archive_root in (evidence_root, trace_root):
        if target == archive_root or archive_root in target.parents:
            raise ValueError("restore destination must be outside the source archive directories")

    evidence_segments = _discover_segments(
        evidence_archive,
        journal_kind="evidence",
        schema_version=EVIDENCE_SCHEMA_VERSION,
    )
    trace_segments = _discover_segments(
        trace_archive,
        journal_kind="decision_trace",
        schema_version=TRACE_SCHEMA_VERSION,
    )

    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{target.name}.restore-", dir=target.parent
    ) as temporary_directory:
        staged_directory = Path(temporary_directory) / "restored"
        staged_directory.mkdir()
        evidence_path = staged_directory / "evidence.sqlite"
        trace_path = staged_directory / "decision-traces.sqlite"

        evidence_report: ArchiveChainRestoreReport = restore_archive_chain(
            evidence_segments,
            evidence_path,
            journal_kind="evidence",
            schema_version=EVIDENCE_SCHEMA_VERSION,
            verify_segment=_verify_evidence_segment,
        )
        trace_report: ArchiveChainRestoreReport = restore_archive_chain(
            trace_segments,
            trace_path,
            journal_kind="decision_trace",
            schema_version=TRACE_SCHEMA_VERSION,
            verify_segment=_verify_trace_segment,
        )

        evidence_store = SQLiteEvidenceStore(evidence_path)
        trace_store = SQLiteDecisionTraceStore(trace_path)
        evidence_verification = evidence_store.verify()
        if not evidence_verification.valid:
            raise ValueError(
                f"restored evidence journal failed verification: {evidence_verification.error}"
            )
        trace_verification = trace_store.verify(evidence_store=evidence_store)
        if not trace_verification.valid:
            raise ValueError(
                f"restored decision-trace journal failed verification: {trace_verification.error}"
            )

        _fsync_file(evidence_path)
        _fsync_file(trace_path)
        _fsync_directory(staged_directory)
        _rename_directory_no_replace(staged_directory, target)
        _fsync_directory(target.parent)

    return PairedJournalRestoreReport(
        destination=str(target),
        evidence_segments_checked=evidence_report.segments_checked,
        evidence_records_restored=evidence_report.records_restored,
        evidence_head_sha256=evidence_report.head_sha256,
        trace_segments_checked=trace_report.segments_checked,
        trace_records_restored=trace_report.records_restored,
        trace_head_sha256=trace_report.head_sha256,
        traces_evidence_checked=trace_verification.evidence_checked,
    )
