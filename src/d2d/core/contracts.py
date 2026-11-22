"""Typed, provider-independent contracts for evidence-backed decisions."""
from datetime import datetime
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
    """Immutable server-created record of an execution and its captured output."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(min_length=1)
    sql: str = Field(min_length=1)
    dialect: str = Field(min_length=1)
    status: EvidenceStatus
    columns: tuple[str, ...] = ()
    rows: tuple[tuple[Any, ...], ...] = ()
    # Number of rows captured in this evidence object, never the total match count.
    row_count: int = Field(ge=0)
    execution_ms: float | None = Field(default=None, ge=0)
    data_version: str | None = None
    source_id: str | None = None
    schema_fingerprint: str | None = None
    query_sha256: str | None = None
    result_sha256: str | None = None
    executed_at: datetime | None = None
    output_bytes: int | None = Field(default=None, ge=0)
    truncated: bool = False

    @model_validator(mode="after")
    def validate_execution_metadata(self) -> "Evidence":
        if self.status is EvidenceStatus.NOT_EXECUTED:
            if self.rows or self.row_count:
                raise ValueError("unexecuted evidence cannot contain result rows")
            if self.executed_at is not None or self.result_sha256 is not None:
                raise ValueError("unexecuted evidence cannot claim execution metadata")
        else:
            if self.row_count != len(self.rows):
                raise ValueError("row_count must equal captured rows in this evidence")
            if self.executed_at is None or self.query_sha256 is None or self.result_sha256 is None:
                raise ValueError("executed evidence requires timestamp and content hashes")
            if self.output_bytes is None:
                raise ValueError("executed evidence requires output byte accounting")
            if len(self.columns) == 0 and self.rows:
                raise ValueError("rows cannot exist without column metadata")
            if any(len(row) != len(self.columns) for row in self.rows):
                raise ValueError("each captured row must match the column count")
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

    @model_validator(mode="after")
    def validate_unique_evidence_ids(self) -> "Decision":
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("decision evidence IDs must be unique")
        return self


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
        if len(evidence_ids) != len(self.evidence):
            raise ValueError("response evidence IDs must be unique")
        if self.decision is not None:
            missing = set(self.decision.evidence_ids) - evidence_ids
            if missing:
                raise ValueError(f"decision references missing evidence: {sorted(missing)}")
        if self.insufficient_data and self.decision is not None:
            raise ValueError("an insufficient-data response cannot contain a recommendation")
        return self


class DecisionClaim(BaseModel):
    """One externally presented decision claim and the evidence supporting it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_evidence_ids(self) -> "DecisionClaim":
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("claim evidence IDs must be unique")
        return self


class EvidenceBinding(BaseModel):
    """Immutable query and source provenance captured into a decision trace."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(min_length=1)
    query_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_id: str = Field(min_length=1)
    schema_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    data_version: str | None = None


class DecisionTrace(BaseModel):
    """Tamper-evident snapshot linking question, planner, claims, and evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    trace_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    planner_version: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    created_at: datetime
    decision: Decision
    claims: tuple[DecisionClaim, ...] = Field(min_length=1)
    evidence: tuple[EvidenceBinding, ...] = Field(min_length=1)
    trace_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_trace_references(self) -> "DecisionTrace":
        evidence_ids = {item.evidence_id for item in self.evidence}
        if len(evidence_ids) != len(self.evidence):
            raise ValueError("trace evidence IDs must be unique")
        claim_ids = [claim.claim_id for claim in self.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("claim IDs must be unique")
        claimed_ids = {item for claim in self.claims for item in claim.evidence_ids}
        if claimed_ids - evidence_ids:
            raise ValueError("claim references evidence absent from trace")
        if set(self.decision.evidence_ids) != claimed_ids:
            raise ValueError("decision evidence IDs must exactly match claim evidence")
        if set(self.decision.evidence_ids) != evidence_ids:
            raise ValueError("trace must contain exactly the decision evidence")
        if self.created_at.tzinfo is None:
            raise ValueError("trace timestamp must be timezone-aware")
        return self
