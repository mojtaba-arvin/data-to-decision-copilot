from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from d2d.core.evidence import create_execution_evidence
from d2d.core.evidence_store import SQLiteEvidenceStore
from d2d.core.journal_chain import GENESIS, restore_archive_chain, verify_archive_chain
from d2d.core.sql_executor import QueryResult


def _canonical_for_test(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


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
) -> tuple[Path, Path, str, str]:
    database = directory / f"{name}.sqlite"
    manifest = directory / f"{name}.manifest.json"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE evidence_journal ("
        "sequence INTEGER PRIMARY KEY, evidence_id TEXT NOT NULL, "
        "payload TEXT NOT NULL, record_sha256 TEXT NOT NULL, "
        "previous_sha256 TEXT NOT NULL, entry_sha256 TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE journal_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    connection.executemany(
        "INSERT INTO journal_meta(key, value) VALUES (?, ?)",
        [
            ("schema_version", "1"),
            ("checkpoint_schema_version", "1"),
            ("checkpoint_sequence", str(first - 1)),
            ("checkpoint_sha256", previous_entry),
        ],
    )
    previous = previous_entry
    entries: list[tuple[int, str]] = []
    for sequence in range(first, last + 1):
        evidence_id = f"evidence-{sequence}"
        record_payload = f"payload-{sequence}"
        record_digest = hashlib.sha256(record_payload.encode()).hexdigest()
        entry_digest = hashlib.sha256(
            _canonical_for_test(
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
            (sequence, evidence_id, record_payload, record_digest, previous, entry_digest),
        )
        entries.append((sequence, entry_digest))
        previous = entry_digest
    connection.commit()
    connection.close()

    payload: dict[str, Any] = {
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
    digest = hashlib.sha256(_canonical_for_test(payload)).hexdigest()
    manifest.write_bytes(_canonical_for_test({**payload, "manifest_sha256": digest}) + b"\n")
    return database, manifest, digest, entries[-1][1]


def refresh_manifest(database: Path, manifest: Path) -> None:
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["database_bytes"] = database.stat().st_size
    document["database_sha256"] = hashlib.sha256(database.read_bytes()).hexdigest()
    payload = {key: value for key, value in document.items() if key != "manifest_sha256"}
    document["manifest_sha256"] = hashlib.sha256(_canonical_for_test(payload)).hexdigest()
    manifest.write_bytes(_canonical_for_test(document) + b"\n")


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
            _canonical_for_test(
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
        previous_entry=first[3], previous_manifest=first[2],
    )

    report = verify_archive_chain(
        [(first[0], first[1]), (second[0], second[1])],
        journal_kind="evidence", schema_version=1, verify_segment=segment_verifier,
    )

    assert report.valid
    assert report.segments_checked == 2
    assert report.records_checked == 4
    assert report.last_sequence == 4
    assert report.head_sha256 == second[3]


def test_rejects_sequence_gap(tmp_path: Path):
    first = make_segment(
        tmp_path, name="segment-1", first=1, last=2,
        previous_entry=GENESIS, previous_manifest=GENESIS,
    )
    second = make_segment(
        tmp_path, name="segment-2", first=4, last=5,
        previous_entry=first[3], previous_manifest=first[2],
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
        previous_entry=first[3], previous_manifest=first[2],
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


def test_rejects_record_tampering_even_when_manifest_is_recomputed(tmp_path: Path):
    segment = make_segment(
        tmp_path, name="segment-1", first=1, last=1,
        previous_entry=GENESIS, previous_manifest=GENESIS,
    )
    with sqlite3.connect(segment[0]) as connection:
        connection.execute(
            "UPDATE evidence_journal SET payload = 'forged' WHERE sequence = 1"
        )
    refresh_manifest(segment[0], segment[1])

    report = verify_archive_chain(
        [(segment[0], segment[1])],
        journal_kind="evidence", schema_version=1, verify_segment=segment_verifier,
    )
    assert not report.valid
    assert "journal segment verification failed" in (report.error or "")


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


def test_restores_full_chain_to_new_database_without_rewriting_hashes(tmp_path: Path):
    first = make_segment(
        tmp_path, name="restore-segment-1", first=1, last=2,
        previous_entry=GENESIS, previous_manifest=GENESIS,
    )
    second = make_segment(
        tmp_path, name="restore-segment-2", first=3, last=4,
        previous_entry=first[3], previous_manifest=first[2],
    )
    destination = tmp_path / "restored-full-journal.sqlite"

    report = restore_archive_chain(
        [(first[0], first[1]), (second[0], second[1])],
        destination,
        journal_kind="evidence",
        schema_version=1,
        verify_segment=segment_verifier,
    )

    assert report.destination == str(destination)
    assert report.segments_checked == 2
    assert report.records_restored == 4
    assert report.last_sequence == 4
    assert report.head_sha256 == second[3]
    assert segment_verifier(destination, 1, GENESIS).valid
    with sqlite3.connect(destination) as connection:
        rows = connection.execute(
            "SELECT sequence, previous_sha256, entry_sha256 "
            "FROM evidence_journal ORDER BY sequence"
        ).fetchall()
    assert [row[0] for row in rows] == [1, 2, 3, 4]
    assert rows[2][1] == rows[1][2]
    assert rows[3][1] == rows[2][2]


def test_restore_refuses_existing_destination(tmp_path: Path):
    segment = make_segment(
        tmp_path, name="existing-destination-segment", first=1, last=1,
        previous_entry=GENESIS, previous_manifest=GENESIS,
    )
    destination = tmp_path / "existing.sqlite"
    destination.write_bytes(b"operator data")

    try:
        restore_archive_chain(
            [(segment[0], segment[1])],
            destination,
            journal_kind="evidence",
            schema_version=1,
            verify_segment=segment_verifier,
        )
    except FileExistsError:
        pass
    else:
        raise AssertionError("restore must not overwrite an existing destination")
    assert destination.read_bytes() == b"operator data"


def test_chain_rejects_checkpoint_that_disagrees_with_manifest_predecessor(
    tmp_path: Path,
):
    first = make_segment(
        tmp_path, name="checkpoint-segment-1", first=1, last=1,
        previous_entry=GENESIS, previous_manifest=GENESIS,
    )
    second = make_segment(
        tmp_path, name="checkpoint-segment-2", first=2, last=2,
        previous_entry=first[3], previous_manifest=first[2],
    )
    with sqlite3.connect(second[0]) as connection:
        connection.execute(
            "UPDATE journal_meta SET value = ? WHERE key = 'checkpoint_sha256'",
            ("f" * 64,),
        )
    refresh_manifest(second[0], second[1])

    report = verify_archive_chain(
        [(first[0], first[1]), (second[0], second[1])],
        journal_kind="evidence",
        schema_version=1,
        verify_segment=segment_verifier,
    )
    assert not report.valid
    assert "checkpoint does not match manifest predecessor" in (report.error or "")


def _store_evidence(value: int):
    return create_execution_evidence(
        sql=f"SELECT {value} AS value",
        dialect="duckdb",
        result=QueryResult(
            columns=("value",), rows=((value,),), captured_rows=1,
            output_bytes=20, truncated=False,
        ),
        source_id="restore-test",
        schema_fingerprint="a" * 64,
        execution_ms=1.0,
    )


def _write_store_segment_manifest(
    database: Path,
    manifest: Path,
    *,
    segment_id: str,
    previous_entry: str,
    previous_manifest: str,
) -> tuple[str, str]:
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT sequence, previous_sha256, entry_sha256 "
            "FROM evidence_journal ORDER BY sequence"
        ).fetchall()
    payload: dict[str, Any] = {
        "format_version": 2,
        "journal_kind": "evidence",
        "database_file": database.name,
        "schema_version": 1,
        "segment_id": segment_id,
        "first_sequence": int(rows[0][0]),
        "last_sequence": int(rows[-1][0]),
        "records": len(rows),
        "first_previous_sha256": previous_entry,
        "first_entry_sha256": str(rows[0][2]),
        "last_entry_sha256": str(rows[-1][2]),
        "previous_manifest_sha256": previous_manifest,
        "database_bytes": database.stat().st_size,
        "database_sha256": hashlib.sha256(database.read_bytes()).hexdigest(),
    }
    digest = hashlib.sha256(_canonical_for_test(payload)).hexdigest()
    manifest.write_bytes(
        _canonical_for_test({**payload, "manifest_sha256": digest}) + b"\\n"
    )
    return digest, str(rows[-1][2])


def test_restores_real_evidence_store_segments_and_reopens_as_genesis_journal(
    tmp_path: Path,
):
    archive_directory = tmp_path / "archive"
    archive_directory.mkdir()

    first_live = SQLiteEvidenceStore(tmp_path / "first-live.sqlite")
    first_live.append(_store_evidence(1))
    first_live.append(_store_evidence(2))
    first_live.initialize_checkpoint_metadata()
    first_database = archive_directory / "segment-1.sqlite"
    first_live.backup(first_database)
    first_manifest = archive_directory / "segment-1.manifest.json"
    first_manifest_hash, first_head = _write_store_segment_manifest(
        first_database,
        first_manifest,
        segment_id="real-evidence-1",
        previous_entry=GENESIS,
        previous_manifest=GENESIS,
    )

    second_live_path = tmp_path / "second-live.sqlite"
    second_live = SQLiteEvidenceStore(second_live_path)
    with sqlite3.connect(second_live_path) as connection:
        connection.executemany(
            "INSERT INTO journal_meta(key, value) VALUES (?, ?)",
            [
                ("checkpoint_schema_version", "1"),
                ("checkpoint_sequence", "2"),
                ("checkpoint_sha256", first_head),
            ],
        )
    second_live = SQLiteEvidenceStore(
        second_live_path, trusted_checkpoint=(2, first_head)
    )
    second_live.append(_store_evidence(3))
    second_live.append(_store_evidence(4))
    second_database = archive_directory / "segment-2.sqlite"
    second_live.backup(second_database)
    second_manifest = archive_directory / "segment-2.manifest.json"
    _write_store_segment_manifest(
        second_database,
        second_manifest,
        segment_id="real-evidence-2",
        previous_entry=first_head,
        previous_manifest=first_manifest_hash,
    )

    def verify_real_segment(path: Path, first: int, previous: str):
        trusted = None if first == 1 else (first - 1, previous)
        store = SQLiteEvidenceStore(path, trusted_checkpoint=trusted)
        return store.verify_segment(
            start_sequence=first, previous_sha256=previous
        )

    destination = tmp_path / "restored-real-evidence.sqlite"
    report = restore_archive_chain(
        [(first_database, first_manifest), (second_database, second_manifest)],
        destination,
        journal_kind="evidence",
        schema_version=1,
        verify_segment=verify_real_segment,
    )

    assert report.records_restored == 4
    restored = SQLiteEvidenceStore(destination)
    assert restored.count() == 4
    assert restored.verify().valid
