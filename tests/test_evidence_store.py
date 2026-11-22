import sqlite3
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
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
