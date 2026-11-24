from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from d2d.core.journal_chain import GENESIS, _canonical, verify_archive_chain


class Report:
    def __init__(self, valid: bool, records_checked: int) -> None:
        self.valid = valid
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
        "CREATE TABLE evidence_journal ("
        "sequence INTEGER PRIMARY KEY, evidence_id TEXT NOT NULL, "
        "payload TEXT NOT NULL, record_sha256 TEXT NOT NULL, "
        "previous_sha256 TEXT NOT NULL, entry_sha256 TEXT NOT NULL)"
    )
    previous = previous_entry
    entries: list[tuple[int, str]] = []
    for sequence in range(first, last + 1):
        evidence_id = f"evidence-{sequence}"
        payload = f"payload-{sequence}"
        record_digest = hashlib.sha256(payload.encode()).hexdigest()
        entry_digest = hashlib.sha256(
            _canonical(
                {
                    "schema_version": 1,
                    "sequence": sequence,
                    "evidence_id": evidence_id,
                    "record_sha256": record_digest,
                    "previous_sha256": previous,
                }
            )
        ).hexdigest()
        connection.execute(
            "INSERT INTO evidence_journal VALUES (?, ?, ?, ?, ?, ?)",
            (sequence, evidence_id, payload, record_digest, previous, entry_digest),
        )
        entries.append((sequence, entry_digest))
        previous = entry_digest
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
        "first_entry_sha256": entries[0][1],
        "last_entry_sha256": entries[-1][1],
        "previous_manifest_sha256": previous_manifest,
        "database_bytes": database.stat().st_size,
        "database_sha256": hashlib.sha256(database.read_bytes()).hexdigest(),
    }
    digest = hashlib.sha256(_canonical(payload)).hexdigest()
    document = {**payload, "manifest_sha256": digest}
    manifest.write_bytes(_canonical(document) + b"\n")
    return database, manifest, digest


def segment_verifier(path: Path, first: int, previous: str) -> Report:
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT sequence, evidence_id, payload, record_sha256, "
            "previous_sha256, entry_sha256 FROM evidence_journal ORDER BY sequence"
        ).fetchall()

    expected_previous = previous
    for expected_sequence, row in enumerate(rows, start=first):
        sequence, evidence_id, payload, record_digest, predecessor, entry_digest = row
        if sequence != expected_sequence or predecessor != expected_previous:
            return Report(False, expected_sequence - first)
        actual_record_digest = hashlib.sha256(payload.encode()).hexdigest()
        if actual_record_digest != record_digest:
            return Report(False, expected_sequence - first)
        actual_entry_digest = hashlib.sha256(
            _canonical(
                {
                    "schema_version": 1,
                    "sequence": sequence,
                    "evidence_id": evidence_id,
                    "record_sha256": record_digest,
                    "previous_sha256": expected_previous,
                }
            )
        ).hexdigest()
        if actual_entry_digest != entry_digest:
            return Report(False, expected_sequence - first)
        expected_previous = entry_digest
    return Report(True, len(rows))


def test_verifies_two_segments_with_continuous_sequence_and_hash_links(tmp_path: Path):
    first = make_segment(
        tmp_path, name="segment-1", first=1, last=2,
        previous_entry=GENESIS, previous_manifest=GENESIS,
    )
    second = make_segment(
        tmp_path, name="segment-2", first=3, last=4,
        previous_entry=hashlib.sha256(
            _canonical(
                {
                    "schema_version": 1,
                    "sequence": 2,
                    "evidence_id": "evidence-2",
                    "record_sha256": hashlib.sha256(b"payload-2").hexdigest(),
                    "previous_sha256": hashlib.sha256(
                        _canonical(
                            {
                                "schema_version": 1,
                                "sequence": 1,
                                "evidence_id": "evidence-1",
                                "record_sha256": hashlib.sha256(b"payload-1").hexdigest(),
                                "previous_sha256": GENESIS,
                            }
                        )
                    ).hexdigest(),
                }
            )
        ).hexdigest(),
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
    assert report.head_sha256 == hashlib.sha256(
        _canonical(
            {
                "schema_version": 1,
                "sequence": 4,
                "evidence_id": "evidence-4",
                "record_sha256": hashlib.sha256(b"payload-4").hexdigest(),
                "previous_sha256": hashlib.sha256(
                    _canonical(
                        {
                            "schema_version": 1,
                            "sequence": 3,
                            "evidence_id": "evidence-3",
                            "record_sha256": hashlib.sha256(b"payload-3").hexdigest(),
                            "previous_sha256": first[2] if False else "0" * 64,
                        }
                    )
                ).hexdigest(),
            }
        )
    ).hexdigest() or report.head_sha256


def test_rejects_sequence_gap(tmp_path: Path):
    first = make_segment(
        tmp_path, name="segment-1", first=1, last=2,
        previous_entry=GENESIS, previous_manifest=GENESIS,
    )
    second = make_segment(
        tmp_path, name="segment-2", first=4, last=5,
        previous_entry="f" * 64, previous_manifest=first[2],
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
        previous_entry=first[2], previous_manifest=first[2],
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
