"""Typed, provider-independent contracts for evidence-backed decisions."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EvidenceStatus(StrEnum):
    EXECUTED = "executed"
    NOT_EXECUTED = "not_executed"


class Evidence(BaseModel):
    """A query and its observed result metadata; values are never inferred."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(min_length=1)
    sql: str = Field(min_length=1)
    dialect: str = Field(min_length=1)
    status: EvidenceStatus
    columns: tuple[str, ...] = ()
    rows: tuple[dict[str, Any], ...] = ()
    row_count: int = Field(ge=0)
    execution_ms: float | None = Field(default=None, ge=0)
    data_version: str | None = None

    @model_validator(mode="after")
    def require_rows_for_executed_evidence(self) -> "Evidence":
        if self.status is EvidenceStatus.NOT_EXECUTED and self.rows:
            raise ValueError("unexecuted evidence cannot contain result rows")
        if self.status is EvidenceStatus.EXECUTED and self.row_count != len(self.rows):
            raise ValueError("row_count must match the captured rows in this contract")
        return self


class Alternative(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: str = Field(min_length=1)
    trade_off: str = Field(min_length=1)


class Decision(BaseModel):
    """A recommendation whose claims can be traced to attached evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = ()
    confidence: Confidence
    alternatives: tuple[Alternative, ...] = ()
    reversal_conditions: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ()


class CopilotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    answer: str = Field(min_length=1)
    decision: Decision | None = None
    evidence: tuple[Evidence, ...] = ()
    insufficient_data: bool = False
    clarification_question: str | None = None

    @model_validator(mode="after")
    def validate_evidence_references(self) -> "CopilotResponse":
        evidence_ids = {item.evidence_id for item in self.evidence}
        if self.decision is not None:
            missing = set(self.decision.evidence_ids) - evidence_ids
            if missing:
                raise ValueError(f"decision references missing evidence: {sorted(missing)}")
        if self.insufficient_data and self.decision is not None:
            raise ValueError("an insufficient-data response cannot contain a recommendation")
        return self
