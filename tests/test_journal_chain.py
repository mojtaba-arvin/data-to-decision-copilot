from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path


from d2d.core.journal_chain import GENESIS, _canonical, verify_archive_chain


class Report:
    valid = True

    def __init__(self, records_checked: int) -> None:
        self.records_checked = records_checked


def make_segment(
    directory: Path,
    *,
    name: str,
    first: int,
    last: int,
    previous_entry: str,
    previous_manifest: str,
) -> tuple[Path, Path, str]:
    database = directory / f"{name}.sqlite"
    manifest = directory / f"{name}.manifest.json"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE evidence_journal (sequence INTEGER PRIMARY KEY, entry_sha256 TEXT NOT NULL)"
    )
    hashes = [hashlib.sha256(f"entry-{sequence}".encode()).hexdigest()
              for sequence in range(first, last + 1)]
    connection.executemany(
        "INSERT INTO evidence_journal(sequence, entry_sha256) VALUES (?, ?)",
        list(zip(range(first, last + 1), hashes, strict=True)),
    )
    connection.commit()
    connection.close()

    payload = {
        "format_version": 2,
        "journal_kind": "evidence",
        "database_file": database.name,
        "schema_version": 1,
        "segment_id": name,
        "first_sequence": first,
        "last_sequence": last,
        "records": last - first + 1,
        "first_previous_sha256": previous_entry,
        "first_entry_sha256": hashes[0],
        "last_entry_sha256": hashes[-1],
        "previous_manifest_sha256": previous_manifest,
        "database_bytes": database.stat().st_size,
        "database_sha256": hashlib.sha256(database.read_bytes()).hexdigest(),
    }
    digest = hashlib.sha256(_canonical(payload)).hexdigest()
    document = {**payload, "manifest_sha256": digest}
    manifest.write_bytes(_canonical(document) + b"\n")
    return database, manifest, digest


def segment_verifier(path: Path, first: int, previous: str) -> Report:
    del previous
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT sequence FROM evidence_journal ORDER BY sequence"
        ).fetchall()
    assert rows[0][0] == first
    return Report(len(rows))


def test_verifies_two_segments_with_continuous_sequence_and_hash_links(tmp_path: Path):
    first = make_segment(
        tmp_path, name="segment-1", first=1, last=2,
        previous_entry=GENESIS, previous_manifest=GENESIS,
    )
    second = make_segment(
        tmp_path, name="segment-2", first=3, last=4,
        previous_entry=hashlib.sha256(b"entry-2").hexdigest(),
        previous_manifest=first[2],
    )

    report = verify_archive_chain(
        [(first[0], first[1]), (second[0], second[1])],
        journal_kind="evidence", schema_version=1, verify_segment=segment_verifier,
    )

    assert report.valid
    assert report.segments_checked == 2
    assert report.records_checked == 4
    assert report.last_sequence == 4
    assert report.head_sha256 == hashlib.sha256(b"entry-4").hexdigest()


def test_rejects_sequence_gap(tmp_path: Path):
    first = make_segment(
        tmp_path, name="segment-1", first=1, last=2,
        previous_entry=GENESIS, previous_manifest=GENESIS,
    )
    second = make_segment(
        tmp_path, name="segment-2", first=4, last=5,
        previous_entry=hashlib.sha256(b"entry-2").hexdigest(),
        previous_manifest=first[2],
    )

    report = verify_archive_chain(
        [(first[0], first[1]), (second[0], second[1])],
        journal_kind="evidence", schema_version=1, verify_segment=segment_verifier,
    )

    assert not report.valid
    assert "sequence gap" in (report.error or "")


def test_rejects_reordered_segments(tmp_path: Path):
    first = make_segment(
        tmp_path, name="segment-1", first=1, last=1,
        previous_entry=GENESIS, previous_manifest=GENESIS,
    )
    second = make_segment(
        tmp_path, name="segment-2", first=2, last=2,
        previous_entry=hashlib.sha256(b"entry-1").hexdigest(),
        previous_manifest=first[2],
    )

    report = verify_archive_chain(
        [(second[0], second[1]), (first[0], first[1])],
        journal_kind="evidence", schema_version=1, verify_segment=segment_verifier,
    )

    assert not report.valid
    assert "sequence gap" in (report.error or "")


def test_rejects_database_tampering(tmp_path: Path):
    segment = make_segment(
        tmp_path, name="segment-1", first=1, last=1,
        previous_entry=GENESIS, previous_manifest=GENESIS,
    )
    with segment[0].open("ab") as stream:
        stream.write(b"tamper")

    report = verify_archive_chain(
        [(segment[0], segment[1])],
        journal_kind="evidence", schema_version=1, verify_segment=segment_verifier,
    )

    assert not report.valid
    assert "database size mismatch" in (report.error or "") or "database digest mismatch" in (report.error or "")


def test_rejects_wrong_journal_kind(tmp_path: Path):
    segment = make_segment(
        tmp_path, name="segment-1", first=1, last=1,
        previous_entry=GENESIS, previous_manifest=GENESIS,
    )

    report = verify_archive_chain(
        [(segment[0], segment[1])],
        journal_kind="decision_trace", schema_version=1, verify_segment=segment_verifier,
    )

    assert not report.valid
    assert "journal kind mismatch" in (report.error or "")


def test_rejects_noncanonical_manifest(tmp_path: Path):
    segment = make_segment(
        tmp_path, name="segment-1", first=1, last=1,
        previous_entry=GENESIS, previous_manifest=GENESIS,
    )
    document = json.loads(segment[1].read_text(encoding="utf-8"))
    segment[1].write_text(json.dumps(document, indent=2), encoding="utf-8")

    report = verify_archive_chain(
        [(segment[0], segment[1])],
        journal_kind="evidence", schema_version=1, verify_segment=segment_verifier,
    )

    assert not report.valid
    assert "not canonical" in (report.error or "")
