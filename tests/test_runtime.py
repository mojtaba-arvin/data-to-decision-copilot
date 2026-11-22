from pathlib import Path

import pytest

from d2d.core.contracts import Confidence, Decision, DecisionClaim, Evidence
from d2d.core.decision_trace_store import SQLiteDecisionTraceStore
from d2d.core.evidence import create_execution_evidence
from d2d.core.evidence_store import SQLiteEvidenceStore
from d2d.core.runtime import (
    DecisionPlan,
    DecisionPolicy,
    DecisionRuntime,
    DeterministicPlanner,
    PolicyViolation,
    evaluate_policy,
)
from d2d.core.sql_executor import QueryResult


def make_evidence(value: int = 7, *, truncated: bool = False) -> Evidence:
    return create_execution_evidence(
        sql=f"SELECT {value} AS value",
        dialect="duckdb",
        result=QueryResult(
            columns=("value",), rows=((value,),), captured_rows=1,
            output_bytes=20, truncated=truncated,
        ),
        source_id="warehouse",
        schema_fingerprint="a" * 64,
        execution_ms=1.0,
    )


def make_plan(evidence: Evidence, *, statement: str | None = None) -> DecisionPlan:
    text = statement or f"Observed stock level is {evidence.rows[0][0]} units."
    decision = Decision(
        action="Review replenishment",
        rationale=text,
        evidence_ids=(evidence.evidence_id,),
        confidence=Confidence.MEDIUM,
        reversal_conditions=("Reassess after the next stock update.",),
    )
    claim = DecisionClaim(
        claim_id="stock-level",
        statement=text,
        evidence_ids=(evidence.evidence_id,),
    )
    return DecisionPlan(decision=decision, claims=(claim,))


def test_policy_accepts_grounded_plan_and_rejects_unsupported_number():
    evidence = make_evidence(7)
    policy = DecisionPolicy(policy_id="baseline", version="1")
    accepted = evaluate_policy(make_plan(evidence), evidence=(evidence,), policy=policy)
    rejected = evaluate_policy(
        make_plan(evidence, statement="Observed stock level is 700 units."),
        evidence=(evidence,), policy=policy,
    )
    assert accepted.allowed
    assert not rejected.allowed
    assert "700" in rejected.violations[0]


def test_policy_rejects_truncated_evidence():
    evidence = make_evidence(truncated=True)
    report = evaluate_policy(
        make_plan(evidence), evidence=(evidence,),
        policy=DecisionPolicy(policy_id="strict", version="1"),
    )
    assert not report.allowed
    assert "truncated evidence" in report.violations[0]


def test_policy_reference_changes_when_policy_definition_changes():
    first = DecisionPolicy(policy_id="baseline", version="1")
    second = DecisionPolicy(
        policy_id="baseline", version="1", require_reversal_conditions=True,
    )
    assert first.reference != second.reference


def test_runtime_persists_only_after_policy_passes(tmp_path: Path):
    evidence_store = SQLiteEvidenceStore(tmp_path / "evidence.sqlite")
    trace_store = SQLiteDecisionTraceStore(tmp_path / "traces.sqlite")
    evidence = make_evidence()
    evidence_store.append(evidence)
    planner = DeterministicPlanner(plan_template=make_plan(evidence))
    runtime = DecisionRuntime(
        evidence_store=evidence_store, trace_store=trace_store,
        policy=DecisionPolicy(policy_id="baseline", version="1"),
    )

    result = runtime.run(
        question="What should we do about stock?",
        evidence_ids=(evidence.evidence_id,),
        planner=planner,
        expected_schema_fingerprints={"warehouse": "a" * 64},
    )

    assert result.sequence == 1
    assert result.policy_report.allowed
    assert result.trace.planner_version.startswith("deterministic-proposal-planner@1#sha256:")
    assert result.trace.policy_version.startswith("baseline@1#sha256:")
    assert trace_store.get(result.trace.trace_id) == result.trace


def test_runtime_does_not_persist_rejected_plan(tmp_path: Path):
    evidence_store = SQLiteEvidenceStore(tmp_path / "evidence.sqlite")
    trace_store = SQLiteDecisionTraceStore(tmp_path / "traces.sqlite")
    evidence = make_evidence()
    evidence_store.append(evidence)
    planner = DeterministicPlanner(
        plan_template=make_plan(evidence, statement="Stock level is 700 units.")
    )
    runtime = DecisionRuntime(
        evidence_store=evidence_store, trace_store=trace_store,
        policy=DecisionPolicy(policy_id="baseline", version="1"),
    )

    with pytest.raises(PolicyViolation, match="numeric values absent from evidence"):
        runtime.run(
            question="What should we do?",
            evidence_ids=(evidence.evidence_id,),
            planner=planner,
        )
    assert trace_store.count() == 0


def test_runtime_rejects_stale_schema_before_planning(tmp_path: Path):
    evidence_store = SQLiteEvidenceStore(tmp_path / "evidence.sqlite")
    evidence = make_evidence()
    evidence_store.append(evidence)
    runtime = DecisionRuntime(
        evidence_store=evidence_store,
        trace_store=SQLiteDecisionTraceStore(tmp_path / "traces.sqlite"),
        policy=DecisionPolicy(policy_id="baseline", version="1"),
    )
    planner = DeterministicPlanner(plan_template=make_plan(evidence))

    with pytest.raises(ValueError, match="stale or unknown schema"):
        runtime.run(
            question="What should we do?",
            evidence_ids=(evidence.evidence_id,),
            planner=planner,
            expected_schema_fingerprints={"warehouse": "b" * 64},
        )


def test_deterministic_planner_rejects_evidence_not_in_request():
    evidence = make_evidence()
    planner = DeterministicPlanner(plan_template=make_plan(evidence))
    from d2d.core.runtime import PlannerRequest

    with pytest.raises(ValueError, match="outside its request"):
        planner.plan(PlannerRequest(question="Question", evidence=(make_evidence(8),)))


def test_policy_minimum_counts_only_decision_linked_evidence():
    first = make_evidence(7)
    unrelated = make_evidence(8)
    report = evaluate_policy(
        make_plan(first),
        evidence=(first, unrelated),
        policy=DecisionPolicy(policy_id="two-source", version="1", min_evidence=2),
    )
    assert not report.allowed
    assert "insufficient decision-linked evidence records" in report.violations
