from pathlib import Path

import pytest

from d2d.core.contracts import Alternative, ClaimKind, Confidence, Decision, DecisionClaim, Evidence
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


def test_fact_claim_requires_literal_value_from_linked_evidence():
    evidence = make_evidence(7)
    base = make_plan(evidence)
    fact = base.claims[0].model_copy(
        update={"kind": ClaimKind.FACT, "statement": "Observed stock level is 7 units."}
    )
    supported = DecisionPlan(decision=base.decision, claims=(fact,))
    unsupported_fact = fact.model_copy(update={"statement": "Stock is critically low."})
    unsupported = DecisionPlan(decision=base.decision, claims=(unsupported_fact,))
    policy = DecisionPolicy(policy_id="fact-check", version="1")

    assert evaluate_policy(supported, evidence=(evidence,), policy=policy).allowed
    rejected = evaluate_policy(unsupported, evidence=(evidence,), policy=policy)
    assert not rejected.allowed
    assert any("literal value match" in item for item in rejected.violations)


def test_recommendation_claim_must_match_decision_action():
    evidence = make_evidence()
    base = make_plan(evidence)
    claim = base.claims[0].model_copy(
        update={"kind": ClaimKind.RECOMMENDATION, "statement": "Increase purchasing immediately"}
    )
    plan = DecisionPlan(decision=base.decision, claims=(claim,))
    report = evaluate_policy(
        plan, evidence=(evidence,), policy=DecisionPolicy(policy_id="recommendation", version="1")
    )
    assert not report.allowed
    assert any("must match the decision action" in item for item in report.violations)


def test_high_confidence_requires_two_distinct_evidence_sources():
    first = make_evidence(7)
    second = make_evidence(7).model_copy(update={"source_id": "inventory"})
    base = make_plan(first)
    decision = base.decision.model_copy(
        update={"evidence_ids": (first.evidence_id, second.evidence_id), "confidence": Confidence.HIGH}
    )
    claim = base.claims[0].model_copy(
        update={"evidence_ids": (first.evidence_id, second.evidence_id)}
    )
    plan = DecisionPlan(decision=decision, claims=(claim,))
    policy = DecisionPolicy(policy_id="calibrated", version="1")

    one_source = evaluate_policy(plan, evidence=(first,), policy=policy)
    two_sources = evaluate_policy(plan, evidence=(first, second), policy=policy)
    assert not one_source.allowed
    assert any("two distinct sources" in item for item in one_source.violations)
    assert two_sources.allowed


def test_policy_can_require_decision_alternatives_and_tradeoffs():
    evidence = make_evidence()
    base = make_plan(evidence)
    policy = DecisionPolicy(
        policy_id="options-required", version="1", require_alternatives=True
    )
    rejected = evaluate_policy(base, evidence=(evidence,), policy=policy)
    assert not rejected.allowed
    assert any("requires at least one alternative" in item for item in rejected.violations)

    decision = base.decision.model_copy(
        update={
            "alternatives": (
                Alternative(
                    action="Defer replenishment",
                    trade_off="Preserves cash but increases stockout risk.",
                ),
            )
        }
    )
    accepted = evaluate_policy(
        DecisionPlan(decision=decision, claims=base.claims),
        evidence=(evidence,),
        policy=policy,
    )
    assert accepted.allowed


def test_decision_rejects_alternative_that_duplicates_primary_action():
    evidence = make_evidence()
    base = make_plan(evidence)
    with pytest.raises(ValueError, match="must not duplicate"):
        Decision.model_validate(
            {
                **base.decision.model_dump(),
                "alternatives": (
                    {
                        "action": base.decision.action,
                        "trade_off": "No meaningful difference.",
                    },
                ),
            }
        )


@pytest.mark.parametrize(
    ("value", "statement"),
    [
        ("north", "The region is north."),
        (True, "The control is true."),
    ],
)
def test_fact_claim_matches_string_and_boolean_literals(value: object, statement: str):
    evidence = make_evidence().model_copy(update={"rows": ((value,),)})
    base = make_plan(evidence, statement=statement)
    fact = base.claims[0].model_copy(update={"kind": ClaimKind.FACT})
    plan = DecisionPlan(decision=base.decision, claims=(fact,))
    report = evaluate_policy(
        plan, evidence=(evidence,), policy=DecisionPolicy(policy_id="literal-values", version="1")
    )
    assert report.allowed, report.violations


def test_fact_string_grounding_requires_token_boundaries():
    evidence = make_evidence().model_copy(update={"rows": (("north",),)})
    base = make_plan(evidence, statement="The region is northwest.")
    fact = base.claims[0].model_copy(update={"kind": ClaimKind.FACT})
    report = evaluate_policy(
        DecisionPlan(decision=base.decision, claims=(fact,)),
        evidence=(evidence,),
        policy=DecisionPolicy(policy_id="token-boundary", version="1"),
    )
    assert not report.allowed
    assert any("literal value match" in item for item in report.violations)


def test_policy_rejects_direct_conflicting_fact_claims():
    evidence = make_evidence(100).model_copy(update={"rows": ((100,), (200,)), "row_count": 2})
    base = make_plan(evidence, statement="Revenue is 100.")
    first = base.claims[0].model_copy(
        update={"kind": ClaimKind.FACT, "statement": "Revenue is 100."}
    )
    second = first.model_copy(
        update={"claim_id": "revenue-200", "statement": "Revenue is 200."}
    )
    decision = base.decision.model_copy(update={"rationale": "Review revenue."})
    report = evaluate_policy(
        DecisionPlan(decision=decision, claims=(first, second)),
        evidence=(evidence,),
        policy=DecisionPolicy(policy_id="contradiction", version="1"),
    )
    assert not report.allowed
    assert any("contradictory" in item for item in report.violations)


def test_policy_rejects_explicit_negation_conflict():
    evidence = make_evidence(7)
    base = make_plan(evidence, statement="Stock is available.")
    positive = base.claims[0].model_copy(
        update={"kind": ClaimKind.FACT, "statement": "Stock is available."}
    )
    negative = positive.model_copy(
        update={"claim_id": "stock-unavailable", "statement": "Stock is not available."}
    )
    report = evaluate_policy(
        DecisionPlan(decision=base.decision, claims=(positive, negative)),
        evidence=(evidence,),
        policy=DecisionPolicy(policy_id="negation-conflict", version="1"),
    )
    assert not report.allowed
    assert any("contradictory" in item for item in report.violations)
