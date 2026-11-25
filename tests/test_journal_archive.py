import json
import os
import stat
from pathlib import Path

import pytest

from d2d.core import journal_archive
from d2d.core.evidence import create_execution_evidence
from d2d.core.evidence_store import SQLiteEvidenceStore
from d2d.core.journal_archive import (
    create_archive_manifest,
    restore_archive_snapshot,
    verify_archive_manifest,
)
from d2d.core.sql_executor import QueryResult


def make_store(path: Path) -> SQLiteEvidenceStore:
    store = SQLiteEvidenceStore(path)
    evidence = create_execution_evidence(
        sql="SELECT 7 AS value",
        dialect="duckdb",
        result=QueryResult(
            columns=("value",),
            rows=((7,),),
            captured_rows=1,
            output_bytes=20,
            truncated=False,
        ),
        source_id="warehouse",
        schema_fingerprint="a" * 64,
        execution_ms=1.0,
    )
    store.append(evidence)
    return store


def test_manifest_round_trip_verifies_full_journal_snapshot(tmp_path: Path):
    source = tmp_path / "live.sqlite"
    snapshot = tmp_path / "snapshot.sqlite"
    manifest = tmp_path / "snapshot.manifest.json"
    store = make_store(source)
    store.backup(snapshot)

    created = create_archive_manifest(
        snapshot,
        manifest,
        journal_kind="evidence",
        schema_version=1,
        verify=lambda path: SQLiteEvidenceStore(path).verify(),
    )
    verified = verify_archive_manifest(
        snapshot,
        manifest,
        journal_kind="evidence",
        schema_version=1,
        verify=lambda path: SQLiteEvidenceStore(path).verify(),
    )

    assert created.valid
    assert created.records_checked == 1
    assert verified.valid
    assert verified.manifest_sha256 == created.manifest_sha256


def test_manifest_rejects_database_byte_tampering(tmp_path: Path):
    snapshot = tmp_path / "snapshot.sqlite"
    manifest = tmp_path / "snapshot.manifest.json"
    make_store(tmp_path / "live.sqlite").backup(snapshot)
    create_archive_manifest(
        snapshot,
        manifest,
        journal_kind="evidence",
        schema_version=1,
        verify=lambda path: SQLiteEvidenceStore(path).verify(),
    )
    with snapshot.open("ab") as stream:
        stream.write(b"tamper")

    report = verify_archive_manifest(
        snapshot,
        manifest,
        journal_kind="evidence",
        schema_version=1,
        verify=lambda path: SQLiteEvidenceStore(path).verify(),
    )
    assert not report.valid
    assert "database_bytes" in (report.error or "") or "database_sha256" in (report.error or "")


def test_manifest_rejects_manifest_payload_tampering(tmp_path: Path):
    snapshot = tmp_path / "snapshot.sqlite"
    manifest = tmp_path / "snapshot.manifest.json"
    make_store(tmp_path / "live.sqlite").backup(snapshot)
    create_archive_manifest(
        snapshot,
        manifest,
        journal_kind="evidence",
        schema_version=1,
        verify=lambda path: SQLiteEvidenceStore(path).verify(),
    )
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["records"] = 999
    manifest.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    report = verify_archive_manifest(
        snapshot,
        manifest,
        journal_kind="evidence",
        schema_version=1,
        verify=lambda path: SQLiteEvidenceStore(path).verify(),
    )
    assert not report.valid
    assert "manifest digest mismatch" in (report.error or "")


def test_manifest_is_no_overwrite_and_journal_kind_bound(tmp_path: Path):
    snapshot = tmp_path / "snapshot.sqlite"
    manifest = tmp_path / "snapshot.manifest.json"
    make_store(tmp_path / "live.sqlite").backup(snapshot)
    create_archive_manifest(
        snapshot,
        manifest,
        journal_kind="evidence",
        schema_version=1,
        verify=lambda path: SQLiteEvidenceStore(path).verify(),
    )

    with pytest.raises(FileExistsError):
        create_archive_manifest(
            snapshot,
            manifest,
            journal_kind="evidence",
            schema_version=1,
            verify=lambda path: SQLiteEvidenceStore(path).verify(),
        )

    report = verify_archive_manifest(
        snapshot,
        manifest,
        journal_kind="decision_trace",
        schema_version=1,
        verify=lambda path: SQLiteEvidenceStore(path).verify(),
    )
    assert not report.valid
    assert "journal kind mismatch" in (report.error or "")


def test_restore_publishes_only_a_verified_snapshot(tmp_path: Path):
    snapshot = tmp_path / "snapshot.sqlite"
    manifest = tmp_path / "snapshot.manifest.json"
    destination = tmp_path / "restored" / "snapshot.sqlite"
    make_store(tmp_path / "live.sqlite").backup(snapshot)
    create_archive_manifest(
        snapshot,
        manifest,
        journal_kind="evidence",
        schema_version=1,
        verify=lambda path: SQLiteEvidenceStore(path).verify(),
    )

    restored = restore_archive_snapshot(
        snapshot,
        manifest,
        destination,
        journal_kind="evidence",
        schema_version=1,
        verify=lambda path: SQLiteEvidenceStore(path).verify(),
    )

    assert restored.valid
    assert restored.records_checked == 1
    assert destination.read_bytes() == snapshot.read_bytes()
    assert SQLiteEvidenceStore(destination).verify().valid


def test_restore_refuses_to_overwrite_existing_destination(tmp_path: Path):
    snapshot = tmp_path / "snapshot.sqlite"
    manifest = tmp_path / "snapshot.manifest.json"
    destination = tmp_path / "restored" / "snapshot.sqlite"
    make_store(tmp_path / "live.sqlite").backup(snapshot)
    create_archive_manifest(
        snapshot,
        manifest,
        journal_kind="evidence",
        schema_version=1,
        verify=lambda path: SQLiteEvidenceStore(path).verify(),
    )
    destination.parent.mkdir()
    destination.write_bytes(b"operator data")

    with pytest.raises(FileExistsError):
        restore_archive_snapshot(
            snapshot,
            manifest,
            destination,
            journal_kind="evidence",
            schema_version=1,
            verify=lambda path: SQLiteEvidenceStore(path).verify(),
        )
    assert destination.read_bytes() == b"operator data"


def test_restore_rejects_tampered_archive_without_publishing(tmp_path: Path):
    snapshot = tmp_path / "snapshot.sqlite"
    manifest = tmp_path / "snapshot.manifest.json"
    destination = tmp_path / "restored" / "snapshot.sqlite"
    make_store(tmp_path / "live.sqlite").backup(snapshot)
    create_archive_manifest(
        snapshot,
        manifest,
        journal_kind="evidence",
        schema_version=1,
        verify=lambda path: SQLiteEvidenceStore(path).verify(),
    )
    with snapshot.open("ab") as stream:
        stream.write(b"tamper")

    with pytest.raises(ValueError, match="invalid archive"):
        restore_archive_snapshot(
            snapshot,
            manifest,
            destination,
            journal_kind="evidence",
            schema_version=1,
            verify=lambda path: SQLiteEvidenceStore(path).verify(),
        )
    assert not destination.exists()


def test_manifest_publication_syncs_file_and_parent_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = tmp_path / "snapshot.sqlite"
    manifest = tmp_path / "snapshot.manifest.json"
    make_store(tmp_path / "live.sqlite").backup(snapshot)

    synced_descriptors: list[bool] = []
    real_fsync = os.fsync

    def record_fsync(descriptor: int) -> None:
        synced_descriptors.append(stat.S_ISDIR(os.fstat(descriptor).st_mode))
        real_fsync(descriptor)

    monkeypatch.setattr(journal_archive.os, "fsync", record_fsync)
    report = create_archive_manifest(
        snapshot,
        manifest,
        journal_kind="evidence",
        schema_version=1,
        verify=lambda path: SQLiteEvidenceStore(path).verify(),
    )

    assert report.valid
    assert synced_descriptors == [False, True]
