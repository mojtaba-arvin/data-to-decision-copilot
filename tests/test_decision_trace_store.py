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
