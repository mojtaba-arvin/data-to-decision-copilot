import os
import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import pytest

from d2d.core.contracts import Evidence
from d2d.core.evidence import create_execution_evidence
from d2d.core.evidence_store import SQLiteEvidenceStore
from d2d.core.sql_executor import QueryResult


def make_evidence(value: int = 7) -> Evidence:
    return create_execution_evidence(
        sql=f"SELECT {value} AS value",
        dialect="duckdb",
        result=QueryResult(
            columns=("value",),
            rows=((value,),),
            captured_rows=1,
            output_bytes=20,
            truncated=False,
        ),
        source_id="test-source",
        schema_fingerprint="d" * 64,
        execution_ms=1.0,
    )


def test_append_round_trip_and_verify(tmp_path: Path):
    store = SQLiteEvidenceStore(tmp_path / "evidence.sqlite")
    item = make_evidence()
    assert store.append(item) == 1
    assert store.get(item.evidence_id) == item
    assert store.count() == 1
    assert store.verify().valid


def test_duplicate_ids_are_rejected_atomically(tmp_path: Path):
    store = SQLiteEvidenceStore(tmp_path / "evidence.sqlite")
    item = make_evidence()
    store.append(item)
    with pytest.raises(sqlite3.IntegrityError):
        store.append(item)
    assert store.count() == 1
    assert store.verify().valid


def test_update_and_delete_are_blocked(tmp_path: Path):
    store = SQLiteEvidenceStore(tmp_path / "evidence.sqlite")
    store.append(make_evidence())
    with closing(sqlite3.connect(store.path)) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("UPDATE evidence_journal SET payload = '{}' WHERE sequence = 1")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM evidence_journal WHERE sequence = 1")


def test_concurrent_appends_receive_unique_ordered_sequences(tmp_path: Path):
    store = SQLiteEvidenceStore(tmp_path / "evidence.sqlite")
    items = [make_evidence(index) for index in range(1, 9)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        sequences = list(pool.map(store.append, items))
    assert sorted(sequences) == list(range(1, 9))
    assert store.count() == 8
    assert store.verify().valid


def test_verifier_detects_payload_tampering(tmp_path: Path):
    store = SQLiteEvidenceStore(tmp_path / "evidence.sqlite")
    store.append(make_evidence())
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER evidence_no_update")
        connection.execute(
            "UPDATE evidence_journal SET payload = replace(payload, 'SELECT 7', 'SELECT 8')"
        )
        connection.commit()
    report = store.verify()
    assert not report.valid
    assert report.records_checked == 0
    assert report.error in {
        "invalid evidence: evidence query digest does not match SQL",
        "record digest mismatch",
    }



def test_verified_backup_preserves_evidence_chain_and_never_overwrites(tmp_path: Path) -> None:
    from d2d.core.evidence import create_execution_evidence
    from d2d.core.sql_executor import QueryResult

    source = tmp_path / "evidence.sqlite"
    store = SQLiteEvidenceStore(source)
    evidence = create_execution_evidence(
        sql="SELECT 7 AS value", dialect="duckdb",
        result=QueryResult(columns=("value",), rows=((7,),), captured_rows=1,
                           output_bytes=8, truncated=False),
        source_id="warehouse", schema_fingerprint="a" * 64, execution_ms=1.0,
    )
    store.append(evidence)
    destination = tmp_path / "backups" / "evidence-001.sqlite"
    report = store.backup(destination)
    assert report.records == 1
    assert report.bytes == destination.stat().st_size
    assert len(report.sha256) == 64
    assert store.count() == 1
    assert SQLiteEvidenceStore(destination).verify().valid
    with pytest.raises(FileExistsError):
        store.backup(destination)



def test_verified_backup_fsyncs_snapshot_and_publication_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SQLiteEvidenceStore(tmp_path / "evidence.sqlite")
    store.append(make_evidence())
    destination = tmp_path / "backups" / "evidence-durable.sqlite"
    original_fsync = os.fsync
    synced_modes: list[int] = []

    def recording_fsync(descriptor: int) -> None:
        synced_modes.append(os.fstat(descriptor).st_mode)
        original_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", recording_fsync)
    report = store.backup(destination)

    assert report.path == str(destination.resolve())
    assert any(stat.S_ISREG(mode) for mode in synced_modes)
    assert any(stat.S_ISDIR(mode) for mode in synced_modes)
    assert SQLiteEvidenceStore(destination).verify().valid


def test_checkpoint_aware_verifier_accepts_non_genesis_segment(tmp_path: Path):
    store = SQLiteEvidenceStore(tmp_path / "evidence.sqlite")
    store.append(make_evidence(1))
    store.append(make_evidence(2))
    with closing(sqlite3.connect(store.path)) as connection:
        predecessor = connection.execute(
            "SELECT entry_sha256 FROM evidence_journal WHERE sequence = 1"
        ).fetchone()[0]
        connection.execute("DROP TRIGGER evidence_no_delete")
        connection.execute("DELETE FROM evidence_journal WHERE sequence = 1")
        connection.commit()

    assert store.verify_segment(start_sequence=2, previous_sha256=predecessor).valid
    rejected = store.verify_segment(start_sequence=2, previous_sha256="0" * 64)
    assert not rejected.valid
    assert rejected.error == "previous-entry link mismatch"


def test_checkpoint_metadata_preserves_sequence_after_archived_prefix(tmp_path: Path):
    store = SQLiteEvidenceStore(tmp_path / "checkpoint-evidence.sqlite")
    store.append(make_evidence(1))
    store.append(make_evidence(2))
    assert store.initialize_checkpoint_metadata() == (0, "0" * 64)

    with closing(sqlite3.connect(store.path)) as connection:
        head = connection.execute(
            "SELECT entry_sha256 FROM evidence_journal WHERE sequence = 2"
        ).fetchone()[0]
        connection.execute("DROP TRIGGER evidence_no_delete")
        connection.execute("DELETE FROM evidence_journal")
        connection.execute(
            "UPDATE journal_meta SET value = '2' WHERE key = 'checkpoint_sequence'"
        )
        connection.execute(
            "UPDATE journal_meta SET value = ? WHERE key = 'checkpoint_sha256'", (head,)
        )
        connection.commit()

    assert store.verify().valid
    assert store.append(make_evidence(3)) == 3
    assert store.verify().valid
    assert store.count() == 1


def test_checkpoint_metadata_initialization_refuses_invalid_journal(tmp_path: Path):
    store = SQLiteEvidenceStore(tmp_path / "invalid-checkpoint-evidence.sqlite")
    store.append(make_evidence())
    with closing(sqlite3.connect(store.path)) as connection:
        connection.execute("DROP TRIGGER evidence_no_update")
        connection.execute(
            "UPDATE evidence_journal SET payload = '{}' WHERE sequence = 1"
        )
        connection.commit()

    with pytest.raises(ValueError, match="invalid journal"):
        store.initialize_checkpoint_metadata()
    with closing(sqlite3.connect(store.path)) as connection:
        keys = connection.execute(
            "SELECT COUNT(*) FROM journal_meta WHERE key LIKE 'checkpoint_%'"
        ).fetchone()[0]
    assert keys == 0
