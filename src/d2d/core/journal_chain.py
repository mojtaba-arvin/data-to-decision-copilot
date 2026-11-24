"""Offline verification of ordered, checkpoint-linked journal archive segments.

The verifier delegates record-level validation to the journal store. It owns
manifest integrity, artifact integrity, ordering, and cross-segment links.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, cast

JournalKind = Literal["evidence", "decision_trace"]
GENESIS = "0" * 64
_SEGMENT_FIELDS = {
    "format_version", "journal_kind", "database_file", "schema_version",
    "segment_id", "first_sequence", "last_sequence", "records",
    "first_previous_sha256", "first_entry_sha256", "last_entry_sha256",
    "previous_manifest_sha256", "database_bytes", "database_sha256",
    "manifest_sha256",
}


class SegmentVerification(Protocol):
    @property
    def valid(self) -> bool: ...

    @property
    def records_checked(self) -> int: ...


@dataclass(frozen=True, slots=True)
class ArchiveChainReport:
    """Result of verifying an ordered set of immutable archive segments."""

    valid: bool
    segments_checked: int
    records_checked: int
    last_sequence: int
    head_sha256: str
    manifest_sha256: str | None = None
    error: str | None = None


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _digest(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"invalid {field}")
    return value


def verify_archive_chain(
    segments: Sequence[tuple[str | Path, str | Path]],
    *,
    journal_kind: JournalKind,
    schema_version: int,
    verify_segment: Callable[[Path, int, str], SegmentVerification],
) -> ArchiveChainReport:
    """Verify manifests, SQLite bytes, record ranges, and cross-segment continuity.

    Segments must be supplied oldest first. The callback must validate every
    record using the declared first sequence and predecessor hash. A genesis-only
    journal verifier is not sufficient.
    """
    try:
        if not segments:
            raise ValueError("archive chain must contain at least one segment")
        if journal_kind not in ("evidence", "decision_trace"):
            raise ValueError("unsupported journal kind")
        if schema_version < 1:
            raise ValueError("schema_version must be positive")

        expected_sequence = 1
        expected_previous = GENESIS
        expected_manifest = GENESIS
        total_records = 0
        seen_ids: set[str] = set()
        final_manifest: str | None = None

        for index, (database_value, manifest_value) in enumerate(segments, start=1):
            database = Path(database_value).resolve()
            manifest_path = Path(manifest_value).resolve()
            if not database.is_file() or not manifest_path.is_file():
                raise ValueError(f"segment {index}: database and manifest must exist")
            raw = manifest_path.read_bytes()
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError(f"segment {index}: manifest must be an object")
            document = cast(dict[str, Any], parsed)
            if set(document) != _SEGMENT_FIELDS:
                raise ValueError(f"segment {index}: manifest fields do not match format version 2")
            if raw.rstrip(b"\n") != _canonical(document):
                raise ValueError(f"segment {index}: manifest is not canonical JSON")
            if document["format_version"] != 2:
                raise ValueError(f"segment {index}: unsupported manifest format version")
            if document["journal_kind"] != journal_kind:
                raise ValueError(f"segment {index}: journal kind mismatch")
            if document["database_file"] != database.name:
                raise ValueError(f"segment {index}: database filename mismatch")
            if document["schema_version"] != schema_version:
                raise ValueError(f"segment {index}: schema version mismatch")

            segment_id = document["segment_id"]
            if not isinstance(segment_id, str) or not segment_id.strip() or segment_id in seen_ids:
                raise ValueError(f"segment {index}: missing or duplicate segment ID")
            seen_ids.add(segment_id)

            first, last, records = (
                document["first_sequence"], document["last_sequence"], document["records"]
            )
            if (
                not isinstance(first, int) or isinstance(first, bool)
                or not isinstance(last, int) or isinstance(last, bool)
                or not isinstance(records, int) or isinstance(records, bool)
                or first < 1 or last < first or records != last - first + 1
            ):
                raise ValueError(f"segment {index}: invalid sequence range")

            first_previous = _digest(document["first_previous_sha256"], "first predecessor hash")
            first_entry = _digest(document["first_entry_sha256"], "first entry hash")
            last_entry = _digest(document["last_entry_sha256"], "last entry hash")
            previous_manifest = _digest(
                document["previous_manifest_sha256"], "previous manifest digest"
            )
            claimed_manifest = _digest(document["manifest_sha256"], "manifest digest")
            payload = {key: value for key, value in document.items() if key != "manifest_sha256"}
            actual_manifest = hashlib.sha256(_canonical(payload)).hexdigest()
            if claimed_manifest != actual_manifest:
                raise ValueError(f"segment {index}: manifest digest mismatch")

            if first != expected_sequence:
                raise ValueError(f"segment {index}: sequence gap, overlap, or reordered segment")
            if first_previous != expected_previous:
                raise ValueError(f"segment {index}: cross-segment entry hash mismatch")
            if previous_manifest != expected_manifest:
                raise ValueError(f"segment {index}: previous manifest link mismatch")
            database_bytes = document["database_bytes"]
            if (
                not isinstance(database_bytes, int)
                or isinstance(database_bytes, bool)
                or database_bytes < 1
                or database_bytes != database.stat().st_size
            ):
                raise ValueError(f"segment {index}: database size mismatch")
            database_digest = _digest(document["database_sha256"], "database digest")
            if database_digest != _sha256_file(database):
                raise ValueError(f"segment {index}: database digest mismatch")

            report = verify_segment(database, first, first_previous)
            if not report.valid:
                raise ValueError(f"segment {index}: journal segment verification failed")
            if report.records_checked != records:
                raise ValueError(f"segment {index}: verifier record count mismatch")

            table = "evidence_journal" if journal_kind == "evidence" else "decision_trace_journal"
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
                bounds = connection.execute(
                    f"SELECT MIN(sequence), MAX(sequence), "
                    f"MIN(CASE WHEN sequence = ? THEN entry_sha256 END), "
                    f"MIN(CASE WHEN sequence = ? THEN entry_sha256 END) FROM {table}",
                    (first, last),
                ).fetchone()
            if bounds is None or bounds[0] != first or bounds[1] != last:
                raise ValueError(f"segment {index}: database sequence bounds mismatch")
            if bounds[2] != first_entry or bounds[3] != last_entry:
                raise ValueError(f"segment {index}: database boundary hash mismatch")

            total_records += records
            expected_sequence = last + 1
            expected_previous = last_entry
            expected_manifest = claimed_manifest
            final_manifest = claimed_manifest

        return ArchiveChainReport(
            valid=True, segments_checked=len(segments), records_checked=total_records,
            last_sequence=expected_sequence - 1, head_sha256=expected_previous,
            manifest_sha256=final_manifest,
        )
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError, sqlite3.Error) as exc:
        return ArchiveChainReport(
            valid=False, segments_checked=0, records_checked=0, last_sequence=0,
            head_sha256=GENESIS, error=str(exc),
        )
