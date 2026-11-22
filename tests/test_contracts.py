import pytest
from pydantic import ValidationError

from d2d.core.contracts import (
    Confidence,
    CopilotResponse,
    Decision,
    Evidence,
    EvidenceStatus,
)


def test_decision_must_reference_attached_evidence() -> None:
    decision = Decision(
        action="Review replenishment policy",
        rationale="The observed stockout rate is elevated.",
        evidence_ids=("stockout-query",),
        confidence=Confidence.MEDIUM,
    )
    with pytest.raises(ValidationError, match="missing evidence"):
        CopilotResponse(answer="Review the policy.", decision=decision)


def test_insufficient_data_cannot_recommend_action() -> None:
    decision = Decision(
        action="Increase inventory",
        rationale="Demand may be rising.",
        confidence=Confidence.LOW,
    )
    with pytest.raises(ValidationError, match="cannot contain a recommendation"):
        CopilotResponse(answer="Not enough data.", decision=decision, insufficient_data=True)


def test_executed_evidence_row_count_is_consistent() -> None:
    with pytest.raises(ValidationError, match="row_count"):
        Evidence(
            evidence_id="q1", sql="SELECT 1", dialect="sqlite",
            status=EvidenceStatus.EXECUTED, row_count=2, rows=({"value": 1},),
        )


def test_unexecuted_evidence_cannot_claim_result_rows() -> None:
    with pytest.raises(ValidationError, match="unexecuted evidence"):
        Evidence(
            evidence_id="q1", sql="SELECT 1", dialect="sqlite",
            status=EvidenceStatus.NOT_EXECUTED, row_count=1, rows=({"value": 1},),
        )
