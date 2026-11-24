import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from d2d.core.contracts import (
    Confidence,
    Decision,
    DecisionClaim,
    DecisionTrace,
    Evidence,
)
from d2d.core.decision_trace import create_decision_trace, verify_decision_trace
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


def build_trace(store: SQLiteEvidenceStore) -> tuple[Evidence, DecisionTrace]:
    evidence = make_evidence()
    store.append(evidence)
    decision = Decision(
        action="Review replenishment",
        rationale="Observed stock is below the configured threshold.",
        evidence_ids=(evidence.evidence_id,),
        confidence=Confidence.MEDIUM,
    )
    claim = DecisionClaim(
        claim_id="claim-stock",
        statement="Observed stock is below the configured threshold.",
        evidence_ids=(evidence.evidence_id,),
    )
    trace = create_decision_trace(
        question="Which items need replenishment review?",
        decision=decision,
        claims=(claim,),
        planner_version="planner-1.2.0",
        policy_version="inventory-policy-3",
        evidence_store=store,
    )
    return evidence, trace


def test_trace_links_claim_decision_query_and_source(tmp_path: Path):
    store = SQLiteEvidenceStore(tmp_path / "evidence.sqlite")
    evidence, trace = build_trace(store)

    assert trace.evidence[0].evidence_id == evidence.evidence_id
    assert trace.evidence[0].query_sha256 == evidence.query_sha256
    assert trace.evidence[0].source_id == "warehouse"
    report = verify_decision_trace(
        trace,
        evidence_store=store,
        expected_schema_fingerprints={"warehouse": "a" * 64},
    )
    assert report.valid
    assert report.evidence_checked == 1


def test_trace_rejects_claim_without_evidence_binding(tmp_path: Path):
    store = SQLiteEvidenceStore(tmp_path / "evidence.sqlite")
    evidence = make_evidence()
    store.append(evidence)
    decision = Decision(
        action="Review stock",
        rationale="Evidence exists.",
        evidence_ids=(evidence.evidence_id,),
        confidence=Confidence.LOW,
    )
    claim = DecisionClaim(
        claim_id="unsupported",
        statement="A second unsupported claim.",
        evidence_ids=("missing-evidence",),
    )
    with pytest.raises(ValidationError, match="absent from trace"):
        create_decision_trace(
            question="What should we do?",
            decision=decision,
            claims=(claim,),
            planner_version="planner-1",
            policy_version="policy-1",
            evidence_store=store,
        )


def test_trace_rejects_empty_claim_evidence():
    with pytest.raises(ValidationError, match="at least 1 item"):
        DecisionClaim(
            claim_id="claim-1",
            statement="An unsupported statement.",
            evidence_ids=(),
        )


def test_verifier_detects_trace_mutation(tmp_path: Path):
    store = SQLiteEvidenceStore(tmp_path / "evidence.sqlite")
    _, trace = build_trace(store)
    altered = trace.model_copy(update={"question": "A different question"})
    report = verify_decision_trace(altered, evidence_store=store)
    assert not report.valid
    assert report.error == "decision trace digest mismatch"


def test_verifier_detects_stale_schema(tmp_path: Path):
    store = SQLiteEvidenceStore(tmp_path / "evidence.sqlite")
    _, trace = build_trace(store)
    report = verify_decision_trace(
        trace,
        evidence_store=store,
        expected_schema_fingerprints={"warehouse": "b" * 64},
    )
    assert not report.valid
    assert report.error == "stale or unknown schema: warehouse"


def test_trace_creation_fails_when_journal_is_invalid(tmp_path: Path):
    store = SQLiteEvidenceStore(tmp_path / "evidence.sqlite")
    _, trace = build_trace(store)
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER evidence_no_update")
        connection.execute("UPDATE evidence_journal SET record_sha256 = 'bad'")
        connection.commit()
    with pytest.raises(ValueError, match="journal verification failed"):
        create_decision_trace(
            question=trace.question,
            decision=trace.decision,
            claims=trace.claims,
            planner_version="planner-1",
            policy_version="policy-1",
            evidence_store=store,
        )
