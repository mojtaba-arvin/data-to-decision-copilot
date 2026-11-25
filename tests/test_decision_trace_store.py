import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import pytest

from d2d.core.contracts import Confidence, Decision, DecisionClaim, DecisionTrace, Evidence
from d2d.core.decision_trace import create_decision_trace
from d2d.core.decision_trace_store import SQLiteDecisionTraceStore
from d2d.core.evidence import create_execution_evidence
from d2d.core.evidence_store import SQLiteEvidenceStore
from d2d.core.journal_chain import GENESIS, restore_archive_chain
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
        source_id="warehouse",
        schema_fingerprint="a" * 64,
        execution_ms=1.0,
        data_version="snapshot-42",
    )


def make_trace(evidence_store: SQLiteEvidenceStore, value: int = 7) -> DecisionTrace:
    evidence = make_evidence(value)
    evidence_store.append(evidence)
    decision = Decision(
        action="Review replenishment",
        rationale="Observed stock is below the configured threshold.",
        evidence_ids=(evidence.evidence_id,),
        confidence=Confidence.MEDIUM,
    )
    claim = DecisionClaim(
        claim_id=f"claim-stock-{value}",
        statement="Observed stock is below the configured threshold.",
        evidence_ids=(evidence.evidence_id,),
    )
    return create_decision_trace(
        question="Which items need replenishment review?",
        decision=decision,
        claims=(claim,),
        planner_version="planner-1.2.0",
        policy_version="inventory-policy-3",
        evidence_store=evidence_store,
    )


def test_trace_persists_and_replays_after_store_reopen(tmp_path: Path):
    evidence_store = SQLiteEvidenceStore(tmp_path / "evidence.sqlite")
    trace = make_trace(evidence_store)
    path = tmp_path / "traces.sqlite"

    assert SQLiteDecisionTraceStore(path).append(trace) == 1
    reopened = SQLiteDecisionTraceStore(path)
    assert reopened.get(trace.trace_id) == trace
    replayed = reopened.replay(
        trace.trace_id,
        evidence_store=evidence_store,
        expected_schema_fingerprints={"warehouse": "a" * 64},
    )
    assert replayed == trace
    report = reopened.verify(evidence_store=evidence_store)
    assert report.valid
    assert report.records_checked == 1
    assert report.evidence_checked == 1


def test_duplicate_trace_ids_are_rejected_atomically(tmp_path: Path):
    evidence_store = SQLiteEvidenceStore(tmp_path / "evidence.sqlite")
    trace = make_trace(evidence_store)
    store = SQLiteDecisionTraceStore(tmp_path / "traces.sqlite")
    store.append(trace)

    with pytest.raises(sqlite3.IntegrityError):
        store.append(trace)

    assert store.count() == 1
    assert store.verify().valid


def test_trace_journal_blocks_update_and_delete(tmp_path: Path):
    evidence_store = SQLiteEvidenceStore(tmp_path / "evidence.sqlite")
    trace = make_trace(evidence_store)
    store = SQLiteDecisionTraceStore(tmp_path / "traces.sqlite")
    store.append(trace)

    with closing(sqlite3.connect(store.path)) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE decision_trace_journal SET payload = '{}' WHERE sequence = 1"
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM decision_trace_journal WHERE sequence = 1")


def test_concurrent_trace_appends_get_unique_ordered_sequences(tmp_path: Path):
    evidence_store = SQLiteEvidenceStore(tmp_path / "evidence.sqlite")
    traces = [make_trace(evidence_store, value) for value in range(1, 9)]
    store = SQLiteDecisionTraceStore(tmp_path / "traces.sqlite")

    with ThreadPoolExecutor(max_workers=8) as pool:
        sequences = list(pool.map(store.append, traces))

    assert sorted(sequences) == list(range(1, 9))
    assert store.count() == 8
    assert store.verify().valid


def test_verifier_detects_persisted_payload_tampering(tmp_path: Path):
    evidence_store = SQLiteEvidenceStore(tmp_path / "evidence.sqlite")
    trace = make_trace(evidence_store)
    store = SQLiteDecisionTraceStore(tmp_path / "traces.sqlite")
    store.append(trace)

    with closing(sqlite3.connect(store.path)) as connection:
        connection.execute("DROP TRIGGER decision_trace_no_update")
        connection.execute(
            "UPDATE decision_trace_journal SET payload = replace(payload, 'Review replenishment', 'Altered action')"
        )
        connection.commit()

    report = store.verify()
    assert not report.valid
    assert report.records_checked == 0
    assert report.error == "decision trace digest mismatch"


def test_replay_fails_closed_for_stale_schema(tmp_path: Path):
    evidence_store = SQLiteEvidenceStore(tmp_path / "evidence.sqlite")
    trace = make_trace(evidence_store)
    store = SQLiteDecisionTraceStore(tmp_path / "traces.sqlite")
    store.append(trace)

    with pytest.raises(ValueError, match="stale or unknown schema"):
        store.replay(
            trace.trace_id,
            evidence_store=evidence_store,
            expected_schema_fingerprints={"warehouse": "b" * 64},
        )


def test_append_rejects_modified_in_memory_trace(tmp_path: Path):
    evidence_store = SQLiteEvidenceStore(tmp_path / "evidence.sqlite")
    trace = make_trace(evidence_store)
    modified = trace.model_copy(update={"question": "A different question"})
    store = SQLiteDecisionTraceStore(tmp_path / "traces.sqlite")

    with pytest.raises(ValueError, match="digest mismatch"):
        store.append(modified)

    assert store.count() == 0



def test_verified_backup_preserves_trace_chain(tmp_path: Path) -> None:
    from d2d.core.contracts import DecisionClaim
    from d2d.core.decision_trace import create_decision_trace
    from d2d.core.evidence import create_execution_evidence
    from d2d.core.evidence_store import SQLiteEvidenceStore
    from d2d.core.sql_executor import QueryResult

    evidence_store = SQLiteEvidenceStore(tmp_path / "evidence.sqlite")
    evidence = create_execution_evidence(
        sql="SELECT 7 AS value", dialect="duckdb",
        result=QueryResult(columns=("value",), rows=((7,),), captured_rows=1,
                           output_bytes=8, truncated=False),
        source_id="warehouse", schema_fingerprint="a" * 64, execution_ms=1.0,
    )
    evidence_store.append(evidence)
    trace = create_decision_trace(
        question="What is the observed value?",
        decision=Decision(
            action="Review value", rationale="Observed value is 7.",
            evidence_ids=(evidence.evidence_id,), confidence=Confidence.LOW,
        ),
        claims=(DecisionClaim(
            claim_id="observed-value", statement="Observed value is 7.",
            evidence_ids=(evidence.evidence_id,),
        ),),
        planner_version="test-planner-v1", policy_version="test-policy-v1",
        evidence_store=evidence_store,
    )
    source = tmp_path / "traces.sqlite"
    store = SQLiteDecisionTraceStore(source)
    store.append(trace)
    destination = tmp_path / "backups" / "traces-001.sqlite"
    report = store.backup(destination)
    assert report.records == 1
    assert SQLiteDecisionTraceStore(destination).verify().valid
    assert store.count() == 1



def test_list_verifies_journal_before_returning_trace_summaries(tmp_path: Path) -> None:
    evidence_store = SQLiteEvidenceStore(tmp_path / "list-evidence.sqlite")
    trace = make_trace(evidence_store)
    store = SQLiteDecisionTraceStore(tmp_path / "list-traces.sqlite")
    store.append(trace)
    listed = store.list(evidence_store=evidence_store)
    assert [item.trace_id for item in listed] == [trace.trace_id]
    assert store.list(limit=10, evidence_store=evidence_store)[0].evidence[0].evidence_id == trace.evidence[0].evidence_id
    with pytest.raises(ValueError, match="limit"):
        store.list(limit=0, evidence_store=evidence_store)


def test_checkpoint_aware_verifier_accepts_non_genesis_trace_segment(tmp_path: Path):
    evidence_store = SQLiteEvidenceStore(tmp_path / "segment-evidence.sqlite")
    traces = [make_trace(evidence_store, value) for value in (1, 2)]
    store = SQLiteDecisionTraceStore(tmp_path / "segment-traces.sqlite")
    store.append(traces[0])
    store.append(traces[1])
    with closing(sqlite3.connect(store.path)) as connection:
        predecessor = connection.execute(
            "SELECT entry_sha256 FROM decision_trace_journal WHERE sequence = 1"
        ).fetchone()[0]
        connection.execute("DROP TRIGGER decision_trace_no_delete")
        connection.execute("DELETE FROM decision_trace_journal WHERE sequence = 1")
        connection.commit()

    report = store.verify_segment(
        start_sequence=2, previous_sha256=predecessor, evidence_store=evidence_store
    )
    assert report.valid
    assert report.records_checked == 1
    rejected = store.verify_segment(
        start_sequence=2, previous_sha256="0" * 64, evidence_store=evidence_store
    )
    assert not rejected.valid
    assert rejected.error == "previous-entry link mismatch"


def test_checkpoint_metadata_preserves_trace_sequence_after_archived_prefix(tmp_path: Path):
    evidence_store = SQLiteEvidenceStore(tmp_path / "checkpoint-trace-evidence.sqlite")
    traces = [make_trace(evidence_store, value) for value in (1, 2, 3)]
    store = SQLiteDecisionTraceStore(tmp_path / "checkpoint-traces.sqlite")
    store.append(traces[0])
    store.append(traces[1])
    assert store.initialize_checkpoint_metadata() == (0, "0" * 64)

    with closing(sqlite3.connect(store.path)) as connection:
        head = connection.execute(
            "SELECT entry_sha256 FROM decision_trace_journal WHERE sequence = 2"
        ).fetchone()[0]
        connection.execute("DROP TRIGGER decision_trace_no_delete")
        connection.execute("DELETE FROM decision_trace_journal")
        connection.execute(
            "UPDATE trace_store_meta SET value = '2' WHERE key = 'checkpoint_sequence'"
        )
        connection.execute(
            "UPDATE trace_store_meta SET value = ? WHERE key = 'checkpoint_sha256'", (head,)
        )
        connection.commit()

    assert not store.verify().valid
    with pytest.raises(RuntimeError, match="requires a trusted archive checkpoint"):
        store.append(traces[2])

    trusted_store = SQLiteDecisionTraceStore(
        store.path, trusted_checkpoint=(2, head)
    )
    assert trusted_store.verify().valid
    assert trusted_store.append(traces[2]) == 3
    assert trusted_store.verify().valid
    assert trusted_store.count() == 1


def test_trace_checkpoint_metadata_initialization_refuses_invalid_journal(tmp_path: Path):
    evidence_store = SQLiteEvidenceStore(tmp_path / "invalid-trace-evidence.sqlite")
    trace = make_trace(evidence_store)
    store = SQLiteDecisionTraceStore(tmp_path / "invalid-checkpoint-traces.sqlite")
    store.append(trace)
    with closing(sqlite3.connect(store.path)) as connection:
        connection.execute("DROP TRIGGER decision_trace_no_update")
        connection.execute(
            "UPDATE decision_trace_journal SET payload = '{}' WHERE sequence = 1"
        )
        connection.commit()

    with pytest.raises(ValueError, match="invalid journal"):
        store.initialize_checkpoint_metadata()
    with closing(sqlite3.connect(store.path)) as connection:
        keys = connection.execute(
            "SELECT COUNT(*) FROM trace_store_meta WHERE key LIKE 'checkpoint_%'"
        ).fetchone()[0]
    assert keys == 0



def _write_trace_segment_manifest(
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
            "FROM decision_trace_journal ORDER BY sequence"
        ).fetchall()
    payload = {
        "format_version": 2,
        "journal_kind": "decision_trace",
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
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    manifest.write_bytes(
        json.dumps(
            {**payload, "manifest_sha256": digest},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )
    return digest, str(rows[-1][2])


def test_restores_real_decision_trace_chain_and_replays_against_evidence(
    tmp_path: Path,
):
    archive = tmp_path / "trace-archive"
    archive.mkdir()
    evidence_store = SQLiteEvidenceStore(tmp_path / "evidence.sqlite")

    first_live = SQLiteDecisionTraceStore(tmp_path / "first-traces.sqlite")
    first_traces = [make_trace(evidence_store, value) for value in (1, 2)]
    for trace in first_traces:
        first_live.append(trace)
    first_live.initialize_checkpoint_metadata()
    first_database = archive / "trace-segment-1.sqlite"
    first_live.backup(first_database)
    first_manifest = archive / "trace-segment-1.manifest.json"
    first_manifest_hash, first_head = _write_trace_segment_manifest(
        first_database,
        first_manifest,
        segment_id="decision-trace-1",
        previous_entry=GENESIS,
        previous_manifest=GENESIS,
    )

    second_path = tmp_path / "second-traces.sqlite"
    SQLiteDecisionTraceStore(second_path)
    with sqlite3.connect(second_path) as connection:
        connection.executemany(
            "INSERT INTO trace_store_meta(key, value) VALUES (?, ?)",
            [
                ("checkpoint_schema_version", "1"),
                ("checkpoint_sequence", "2"),
                ("checkpoint_sha256", first_head),
            ],
        )
    second_live = SQLiteDecisionTraceStore(
        second_path, trusted_checkpoint=(2, first_head)
    )
    second_traces = [make_trace(evidence_store, value) for value in (3, 4)]
    for trace in second_traces:
        second_live.append(trace)
    second_database = archive / "trace-segment-2.sqlite"
    second_live.backup(second_database)
    second_manifest = archive / "trace-segment-2.manifest.json"
    _write_trace_segment_manifest(
        second_database,
        second_manifest,
        segment_id="decision-trace-2",
        previous_entry=first_head,
        previous_manifest=first_manifest_hash,
    )

    def verify_trace_segment(path: Path, start: int, previous: str):
        store = SQLiteDecisionTraceStore(
            path, trusted_checkpoint=(start - 1, previous)
        )
        return store.verify_segment(
            start_sequence=start, previous_sha256=previous
        )

    destination = tmp_path / "restored-decision-traces.sqlite"
    report = restore_archive_chain(
        [(first_database, first_manifest), (second_database, second_manifest)],
        destination,
        journal_kind="decision_trace",
        schema_version=1,
        verify_segment=verify_trace_segment,
    )

    assert report.records_restored == 4
    assert report.last_sequence == 4
    restored = SQLiteDecisionTraceStore(destination)
    verification = restored.verify(
        evidence_store=evidence_store,
        expected_schema_fingerprints={"warehouse": "a" * 64},
    )
    assert verification.valid
    assert verification.records_checked == 4
    assert verification.evidence_checked == 4
    for trace in first_traces + second_traces:
        assert restored.replay(
            trace.trace_id,
            evidence_store=evidence_store,
            expected_schema_fingerprints={"warehouse": "a" * 64},
        ) == trace



def _write_evidence_segment_manifest(
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
    payload = {
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
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    manifest.write_bytes(
        json.dumps(
            {**payload, "manifest_sha256": digest},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )
    return digest, str(rows[-1][2])


def test_restores_paired_journals_and_replays_traces_against_restored_evidence(
    tmp_path: Path,
):
    archive = tmp_path / "paired-archive"
    evidence_archive = archive / "evidence"
    trace_archive = archive / "traces"
    evidence_archive.mkdir(parents=True)
    trace_archive.mkdir(parents=True)

    first_evidence = SQLiteEvidenceStore(tmp_path / "first-evidence.sqlite")
    first_traces = SQLiteDecisionTraceStore(tmp_path / "first-traces.sqlite")
    first_trace_records = [make_trace(first_evidence, value) for value in (11, 12)]
    for trace in first_trace_records:
        first_traces.append(trace)
    first_evidence.initialize_checkpoint_metadata()
    first_traces.initialize_checkpoint_metadata()

    evidence_one = evidence_archive / "segment-1.sqlite"
    first_evidence.backup(evidence_one)
    evidence_one_manifest = evidence_archive / "segment-1.manifest.json"
    evidence_one_digest, evidence_one_head = _write_evidence_segment_manifest(
        evidence_one,
        evidence_one_manifest,
        segment_id="paired-evidence-1",
        previous_entry=GENESIS,
        previous_manifest=GENESIS,
    )
    trace_one = trace_archive / "segment-1.sqlite"
    first_traces.backup(trace_one)
    trace_one_manifest = trace_archive / "segment-1.manifest.json"
    trace_one_digest, trace_one_head = _write_trace_segment_manifest(
        trace_one,
        trace_one_manifest,
        segment_id="paired-traces-1",
        previous_entry=GENESIS,
        previous_manifest=GENESIS,
    )

    second_evidence_path = tmp_path / "second-evidence.sqlite"
    SQLiteEvidenceStore(second_evidence_path)
    with sqlite3.connect(second_evidence_path) as connection:
        connection.executemany(
            "INSERT INTO journal_meta(key, value) VALUES (?, ?)",
            [
                ("checkpoint_schema_version", "1"),
                ("checkpoint_sequence", "2"),
                ("checkpoint_sha256", evidence_one_head),
            ],
        )
    second_evidence = SQLiteEvidenceStore(
        second_evidence_path, trusted_checkpoint=(2, evidence_one_head)
    )

    second_trace_path = tmp_path / "second-traces.sqlite"
    SQLiteDecisionTraceStore(second_trace_path)
    with sqlite3.connect(second_trace_path) as connection:
        connection.executemany(
            "INSERT INTO trace_store_meta(key, value) VALUES (?, ?)",
            [
                ("checkpoint_schema_version", "1"),
                ("checkpoint_sequence", "2"),
                ("checkpoint_sha256", trace_one_head),
            ],
        )
    second_traces = SQLiteDecisionTraceStore(
        second_trace_path, trusted_checkpoint=(2, trace_one_head)
    )
    second_trace_records = [make_trace(second_evidence, value) for value in (13, 14)]
    for trace in second_trace_records:
        second_traces.append(trace)

    evidence_two = evidence_archive / "segment-2.sqlite"
    second_evidence.backup(evidence_two)
    evidence_two_manifest = evidence_archive / "segment-2.manifest.json"
    _write_evidence_segment_manifest(
        evidence_two,
        evidence_two_manifest,
        segment_id="paired-evidence-2",
        previous_entry=evidence_one_head,
        previous_manifest=evidence_one_digest,
    )
    trace_two = trace_archive / "segment-2.sqlite"
    second_traces.backup(trace_two)
    trace_two_manifest = trace_archive / "segment-2.manifest.json"
    _write_trace_segment_manifest(
        trace_two,
        trace_two_manifest,
        segment_id="paired-traces-2",
        previous_entry=trace_one_head,
        previous_manifest=trace_one_digest,
    )

    def verify_evidence_segment(path: Path, start: int, previous: str):
        store = SQLiteEvidenceStore(
            path, trusted_checkpoint=(start - 1, previous)
        )
        return store.verify_segment(
            start_sequence=start, previous_sha256=previous
        )

    def verify_trace_segment(path: Path, start: int, previous: str):
        store = SQLiteDecisionTraceStore(
            path, trusted_checkpoint=(start - 1, previous)
        )
        return store.verify_segment(
            start_sequence=start, previous_sha256=previous
        )

    restored_directory = tmp_path / "restored-pair"
    restored_directory.mkdir()
    restored_evidence_path = restored_directory / "evidence.sqlite"
    restored_trace_path = restored_directory / "traces.sqlite"
    evidence_report = restore_archive_chain(
        [(evidence_one, evidence_one_manifest), (evidence_two, evidence_two_manifest)],
        restored_evidence_path,
        journal_kind="evidence",
        schema_version=1,
        verify_segment=verify_evidence_segment,
    )
    trace_report = restore_archive_chain(
        [(trace_one, trace_one_manifest), (trace_two, trace_two_manifest)],
        restored_trace_path,
        journal_kind="decision_trace",
        schema_version=1,
        verify_segment=verify_trace_segment,
    )

    restored_evidence = SQLiteEvidenceStore(restored_evidence_path)
    restored_traces = SQLiteDecisionTraceStore(restored_trace_path)
    assert evidence_report.records_restored == 4
    assert trace_report.records_restored == 4
    verification = restored_traces.verify(
        evidence_store=restored_evidence,
        expected_schema_fingerprints={"warehouse": "a" * 64},
    )
    assert verification.valid
    assert verification.records_checked == 4
    assert verification.evidence_checked == 4
    for trace in first_trace_records + second_trace_records:
        assert restored_traces.replay(
            trace.trace_id,
            evidence_store=restored_evidence,
            expected_schema_fingerprints={"warehouse": "a" * 64},
        ) == trace
