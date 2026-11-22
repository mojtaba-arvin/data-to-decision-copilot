"""Provider-independent planner orchestration and fail-closed decision policy."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from d2d.core.contracts import (
    Confidence,
    Decision,
    DecisionClaim,
    DecisionTrace,
    Evidence,
)
from d2d.core.decision_trace import EvidenceStore, create_decision_trace
from d2d.core.decision_trace_store import SQLiteDecisionTraceStore

_NUMBER = re.compile(
    r"(?<![\\w.])[-+]?(?:\\d{1,3}(?:,\\d{3})+|\\d+)(?:\\.\\d+)?%?(?![\\w.])"
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=str, allow_nan=False,
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _number_key(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    try:
        number = Decimal(str(value)).normalize()
    except InvalidOperation:
        return None
    return format(number, "f")


def _numbers_in_text(value: str) -> set[str]:
    numbers: set[str] = set()
    for match in _NUMBER.findall(value):
        token = match.rstrip("%").replace(",", "")
        try:
            numbers.add(format(Decimal(token).normalize(), "f"))
        except InvalidOperation:
            continue
    return numbers


class PlannerIdentity(BaseModel):
    """Immutable identity of a planner implementation artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    planner_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @property
    def reference(self) -> str:
        return f"{self.planner_id}@{self.version}#sha256:{self.artifact_sha256}"


class DecisionPlan(BaseModel):
    """Structured planner output; all claims must identify their evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: Decision
    claims: tuple[DecisionClaim, ...] = Field(min_length=1)


class PlannerRequest(BaseModel):
    """Only verified, persisted execution evidence is exposed to a planner."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    question: str = Field(min_length=1)
    evidence: tuple[Evidence, ...] = Field(min_length=1)


class Planner(Protocol):
    @property
    def identity(self) -> PlannerIdentity: ...

    def plan(self, request: PlannerRequest) -> DecisionPlan: ...


@dataclass(frozen=True, slots=True)
class DeterministicPlanner:
    """Deterministic adapter for a pre-authored decision proposal.

    This is a contract/reference implementation, not a semantic SQL planner.
    It never invents a recommendation from raw database rows.
    """

    plan_template: DecisionPlan
    planner_id: str = "deterministic-proposal-planner"
    version: str = "1"

    @property
    def identity(self) -> PlannerIdentity:
        artifact = _sha256({
            "planner_id": self.planner_id,
            "version": self.version,
            "plan_template": self.plan_template.model_dump(mode="json"),
        })
        return PlannerIdentity(
            planner_id=self.planner_id,
            version=self.version,
            artifact_sha256=artifact,
        )

    def plan(self, request: PlannerRequest) -> DecisionPlan:
        available = {item.evidence_id for item in request.evidence}
        referenced = set(self.plan_template.decision.evidence_ids)
        if not referenced.issubset(available):
            raise ValueError("planner proposal references evidence outside its request")
        return self.plan_template


class DecisionPolicy(BaseModel):
    """Immutable, content-addressed deterministic policy definition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    min_evidence: int = Field(default=1, ge=1)
    allowed_confidence: tuple[Confidence, ...] = (
        Confidence.LOW, Confidence.MEDIUM, Confidence.HIGH,
    )
    reject_truncated_evidence: bool = True
    require_reversal_conditions: bool = False

    @property
    def definition_sha256(self) -> str:
        return _sha256(self.model_dump(mode="json"))

    @property
    def reference(self) -> str:
        return f"{self.policy_id}@{self.version}#sha256:{self.definition_sha256}"


@dataclass(frozen=True, slots=True)
class PolicyReport:
    allowed: bool
    policy_reference: str
    violations: tuple[str, ...]


class PolicyViolation(ValueError):
    def __init__(self, report: PolicyReport) -> None:
        self.report = report
        super().__init__("decision rejected by policy: " + "; ".join(report.violations))


def evaluate_policy(
    plan: DecisionPlan,
    *,
    evidence: tuple[Evidence, ...],
    policy: DecisionPolicy,
) -> PolicyReport:
    """Apply deterministic evidence, numeric-grounding, and decision checks."""
    violations: list[str] = []
    by_id = {item.evidence_id: item for item in evidence}
    decision = plan.decision
    referenced = set(decision.evidence_ids)

    if len(evidence) < policy.min_evidence:
        violations.append("insufficient evidence records")
    if not referenced:
        violations.append("decision must reference at least one evidence record")
    missing = referenced - set(by_id)
    if missing:
        violations.append(f"decision references unavailable evidence: {sorted(missing)}")
    if decision.confidence not in policy.allowed_confidence:
        violations.append(f"confidence level is not allowed: {decision.confidence.value}")
    if policy.require_reversal_conditions and not decision.reversal_conditions:
        violations.append("decision requires at least one reversal condition")

    claim_ids = [claim.claim_id for claim in plan.claims]
    if len(claim_ids) != len(set(claim_ids)):
        violations.append("claim IDs must be unique")
    claim_evidence = {item for claim in plan.claims for item in claim.evidence_ids}
    if claim_evidence != referenced:
        violations.append("claim evidence references must exactly match decision evidence")
    for claim in plan.claims:
        if not claim.evidence_ids:
            violations.append(f"claim {claim.claim_id} has no evidence references")
        if set(claim.evidence_ids) - set(by_id):
            violations.append(f"claim {claim.claim_id} references unavailable evidence")

    usable = [by_id[item] for item in referenced if item in by_id]
    if policy.reject_truncated_evidence and any(item.truncated for item in usable):
        violations.append("truncated evidence is not allowed by this policy")

    numeric_values = {
        number
        for item in usable
        for row in item.rows
        for value in row
        if (number := _number_key(value)) is not None
    }
    statements: list[tuple[str, str]] = [
        ("decision.action", decision.action),
        ("decision.rationale", decision.rationale),
    ]
    statements.extend((f"claim.{claim.claim_id}", claim.statement) for claim in plan.claims)
    for label, statement in statements:
        unsupported = _numbers_in_text(statement) - numeric_values
        if unsupported:
            violations.append(
                f"{label} contains numeric values absent from evidence: {sorted(unsupported)}"
            )

    return PolicyReport(
        allowed=not violations,
        policy_reference=policy.reference,
        violations=tuple(violations),
    )


@dataclass(frozen=True, slots=True)
class RuntimeResult:
    trace: DecisionTrace
    sequence: int
    policy_report: PolicyReport


class DecisionRuntime:
    """Run a planner, enforce policy, then persist the verified decision trace."""

    def __init__(
        self,
        *,
        evidence_store: EvidenceStore,
        trace_store: SQLiteDecisionTraceStore,
        policy: DecisionPolicy,
    ) -> None:
        self._evidence_store = evidence_store
        self._trace_store = trace_store
        self._policy = policy

    def run(
        self,
        *,
        question: str,
        evidence_ids: tuple[str, ...],
        planner: Planner,
        expected_schema_fingerprints: Mapping[str, str] | None = None,
    ) -> RuntimeResult:
        if not question.strip():
            raise ValueError("question must not be empty")
        if not evidence_ids or len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence_ids must be non-empty and unique")

        journal = self._evidence_store.verify()
        if not journal.valid:
            raise ValueError(f"evidence journal verification failed: {journal.error}")

        evidence_items: list[Evidence] = []
        for evidence_id in evidence_ids:
            item = self._evidence_store.get(evidence_id)
            if item is None:
                raise ValueError(f"requested evidence is unavailable: {evidence_id}")
            if not item.source_id or not item.schema_fingerprint:
                raise ValueError(f"evidence lacks source/schema provenance: {evidence_id}")
            evidence_items.append(item)
        evidence = tuple(evidence_items)

        if expected_schema_fingerprints is not None:
            for item in evidence:
                current = expected_schema_fingerprints.get(item.source_id or "")
                if current != item.schema_fingerprint:
                    raise ValueError(f"stale or unknown schema: {item.source_id}")

        planner_identity = planner.identity
        plan = planner.plan(PlannerRequest(question=question, evidence=evidence))
        if not set(plan.decision.evidence_ids).issubset(set(evidence_ids)):
            raise ValueError("planner returned evidence outside the runtime request")

        report = evaluate_policy(plan, evidence=evidence, policy=self._policy)
        if not report.allowed:
            raise PolicyViolation(report)

        trace = create_decision_trace(
            question=question,
            decision=plan.decision,
            claims=plan.claims,
            planner_version=planner_identity.reference,
            policy_version=self._policy.reference,
            evidence_store=self._evidence_store,
        )
        sequence = self._trace_store.append(trace)
        return RuntimeResult(trace=trace, sequence=sequence, policy_report=report)
