from __future__ import annotations

from dataclasses import replace

import pytest

from d2d.core.journal_segment_chain import (
    GENESIS,
    ArchiveSegmentDescriptor,
    verify_segment_chain,
)


def segment(
    segment_id: str,
    first: int,
    last: int,
    previous_entry: str,
    head: str,
    manifest: str,
    previous_manifest: str | None,
) -> ArchiveSegmentDescriptor:
    return ArchiveSegmentDescriptor(
        segment_id=segment_id,
        journal_kind="evidence",
        first_sequence=first,
        last_sequence=last,
        records=last - first + 1,
        first_previous_sha256=previous_entry,
        last_entry_sha256=head,
        manifest_sha256=manifest,
        previous_manifest_sha256=previous_manifest,
    )


def valid_chain() -> list[ArchiveSegmentDescriptor]:
    first = segment("segment-1", 1, 2, GENESIS, "a" * 64, "b" * 64, None)
    second = segment("segment-2", 3, 4, "a" * 64, "c" * 64, "d" * 64, "b" * 64)
    return [first, second]


def test_verifies_contiguous_segment_sequence_and_hash_links():
    report = verify_segment_chain(valid_chain(), journal_kind="evidence")

    assert report.valid
    assert report.segments_checked == 2
    assert report.records_covered == 4
    assert report.first_sequence == 1
    assert report.last_sequence == 4
    assert report.head_sha256 == "c" * 64
    assert report.head_manifest_sha256 == "d" * 64


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda chain: [chain[0], replace(chain[1], first_sequence=4, last_sequence=5)],
         "sequence gap"),
        (lambda chain: [chain[0], replace(chain[1], first_previous_sha256="e" * 64)],
         "previous-entry hash mismatch"),
        (lambda chain: [chain[0], replace(chain[1], previous_manifest_sha256="e" * 64)],
         "previous-manifest digest mismatch"),
        (lambda chain: [chain[0], replace(chain[1], journal_kind="decision_trace")],
         "journal kind mismatch"),
        (lambda chain: [chain[0], replace(chain[1], manifest_sha256=chain[0].manifest_sha256)],
         "duplicate archive segment manifest digest"),
    ],
)
def test_rejects_broken_cross_segment_links(mutation, message):
    with pytest.raises(ValueError, match=message):
        verify_segment_chain(mutation(valid_chain()), journal_kind="evidence")


def test_rejects_duplicate_segment_ids_and_non_genesis_first_segment():
    chain = valid_chain()
    with pytest.raises(ValueError, match="duplicate archive segment ID"):
        verify_segment_chain([chain[0], replace(chain[1], segment_id=chain[0].segment_id)],
                             journal_kind="evidence")
    with pytest.raises(ValueError, match="sequence gap"):
        verify_segment_chain([replace(chain[0], first_sequence=2, last_sequence=3)],
                             journal_kind="evidence")


def test_rejects_inconsistent_record_count_and_invalid_digest():
    chain = valid_chain()
    with pytest.raises(ValueError, match="record count"):
        verify_segment_chain([replace(chain[0], records=1)], journal_kind="evidence")
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        verify_segment_chain([replace(chain[0], manifest_sha256="NOT-A-DIGEST")],
                             journal_kind="evidence")
