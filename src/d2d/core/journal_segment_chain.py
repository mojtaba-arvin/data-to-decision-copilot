"""Cross-segment continuity checks for independently verified journal archives.

This module validates segment ordering and hash/manifest links. It does not
verify SQLite rows or authenticate manifests; callers must verify each segment
snapshot and manifest before passing its descriptor here.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

JournalKind = Literal["evidence", "decision_trace"]
GENESIS = "0" * 64
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class ArchiveSegmentDescriptor:
    """Continuity metadata extracted from one already-verified manifest."""

    segment_id: str
    journal_kind: JournalKind
    first_sequence: int
    last_sequence: int
    records: int
    first_previous_sha256: str
    last_entry_sha256: str
    manifest_sha256: str
    previous_manifest_sha256: str | None


@dataclass(frozen=True, slots=True)
class SegmentChainReport:
    """Summary of a structurally continuous archive segment chain."""

    valid: bool
    segments_checked: int
    records_covered: int
    first_sequence: int | None
    last_sequence: int | None
    head_sha256: str
    head_manifest_sha256: str | None


def _require_digest(value: str, field: str) -> None:
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")


def verify_segment_chain(
    segments: Sequence[ArchiveSegmentDescriptor],
    *,
    journal_kind: JournalKind,
) -> SegmentChainReport:
    """Validate ordered sequence and hash continuity across archive segments.

    The first segment must begin at genesis. Every later segment must continue
    the prior sequence, entry hash, and manifest digest without gaps or overlap.
    An empty chain is rejected; empty journals are represented by no archive.
    """
    if not segments:
        raise ValueError("archive segment chain must not be empty")

    seen_ids: set[str] = set()
    seen_manifests: set[str] = set()
    expected_sequence = 1
    expected_previous_entry = GENESIS
    expected_previous_manifest: str | None = None
    records_covered = 0

    for index, segment in enumerate(segments):
        if not segment.segment_id.strip():
            raise ValueError(f"segment {index} has an empty segment ID")
        if segment.segment_id in seen_ids:
            raise ValueError("duplicate archive segment ID")
        seen_ids.add(segment.segment_id)

        if segment.journal_kind != journal_kind:
            raise ValueError("archive segment journal kind mismatch")
        if segment.first_sequence < 1 or segment.last_sequence < segment.first_sequence:
            raise ValueError("archive segment sequence range is invalid")
        if segment.records != segment.last_sequence - segment.first_sequence + 1:
            raise ValueError("archive segment record count does not match its sequence range")

        _require_digest(segment.first_previous_sha256, "first_previous_sha256")
        _require_digest(segment.last_entry_sha256, "last_entry_sha256")
        _require_digest(segment.manifest_sha256, "manifest_sha256")
        if segment.previous_manifest_sha256 is not None:
            _require_digest(segment.previous_manifest_sha256, "previous_manifest_sha256")

        if segment.first_sequence != expected_sequence:
            raise ValueError("archive segment sequence gap, overlap, or reordering")
        if segment.first_previous_sha256 != expected_previous_entry:
            raise ValueError("archive segment previous-entry hash mismatch")
        if segment.previous_manifest_sha256 != expected_previous_manifest:
            raise ValueError("archive segment previous-manifest digest mismatch")
        if segment.manifest_sha256 in seen_manifests:
            raise ValueError("duplicate archive segment manifest digest")
        seen_manifests.add(segment.manifest_sha256)

        records_covered += segment.records
        expected_sequence = segment.last_sequence + 1
        expected_previous_entry = segment.last_entry_sha256
        expected_previous_manifest = segment.manifest_sha256

    return SegmentChainReport(
        valid=True,
        segments_checked=len(segments),
        records_covered=records_covered,
        first_sequence=1,
        last_sequence=expected_sequence - 1,
        head_sha256=expected_previous_entry,
        head_manifest_sha256=expected_previous_manifest,
    )
