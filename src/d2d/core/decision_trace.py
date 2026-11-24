"""Build and verify immutable evidence-to-decision lineage records."""
from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from d2d.core.contracts import (
    Decision,
    DecisionClaim,
    DecisionTrace,
    Evidence,
    EvidenceBinding,
)
from d2d.core.evidence_store import VerificationReport


class EvidenceStore(Protocol):
    """Minimal read-only surface required to build or verify a decision trace."""

    def get(self, evidence_id: str) -> Evidence | None: ...

    def verify(self) -> VerificationReport: ...


@dataclass(frozen=True, slots=True)
class TraceVerificationReport:
    valid: bool
    evidence_checked: int
    error: str | None = None


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        allow_nan=False,
    ).encode("utf-8")


def _trace_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _trace_payload(trace: DecisionTrace) -> dict[str, Any]:
    return trace.model_dump(mode="json", exclude={"trace_sha256"})


def create_decision_trace(
    *,
    question: str,
    decision: Decision,
    claims: tuple[DecisionClaim, ...],
    planner_version: str,
    policy_version: str,
    evidence_store: EvidenceStore,
) -> DecisionTrace:
    """Create a trace only from evidence present in a valid evidence journal."""
    journal_report = evidence_store.verify()
    if not journal_report.valid:
        raise ValueError(f"evidence journal verification failed: {journal_report.error}")

    bindings: list[EvidenceBinding] = []
    for evidence_id in decision.evidence_ids:
        item = evidence_store.get(evidence_id)
        if item is None:
            raise ValueError(f"decision references unavailable evidence: {evidence_id}")
        if not item.source_id or not item.schema_fingerprint:
            raise ValueError(f"evidence lacks source/schema provenance: {evidence_id}")
        if not item.query_sha256 or not item.result_sha256:
            raise ValueError(f"evidence lacks execution digests: {evidence_id}")
        bindings.append(
            EvidenceBinding(
                evidence_id=evidence_id,
                query_sha256=item.query_sha256,
                result_sha256=item.result_sha256,
                source_id=item.source_id,
                schema_fingerprint=item.schema_fingerprint,
                data_version=item.data_version,
            )
        )

    trace = DecisionTrace(
        trace_id=str(uuid.uuid4()),
        question=question,
        planner_version=planner_version,
        policy_version=policy_version,
        created_at=datetime.now(UTC),
        decision=decision,
        claims=claims,
        evidence=tuple(bindings),
        trace_sha256="0" * 64,
    )
    return trace.model_copy(update={"trace_sha256": _trace_digest(_trace_payload(trace))})


def verify_decision_trace(
    trace: DecisionTrace,
    *,
    evidence_store: EvidenceStore,
    expected_schema_fingerprints: Mapping[str, str] | None = None,
) -> TraceVerificationReport:
    """Verify trace integrity, journal integrity, evidence identity, and schema freshness."""
    if _trace_digest(_trace_payload(trace)) != trace.trace_sha256:
        return TraceVerificationReport(False, 0, "decision trace digest mismatch")

    journal_report = evidence_store.verify()
    if not journal_report.valid:
        return TraceVerificationReport(
            False, 0, f"evidence journal verification failed: {journal_report.error}"
        )

    checked = 0
    for binding in trace.evidence:
        item = evidence_store.get(binding.evidence_id)
        if item is None:
            return TraceVerificationReport(
                False, checked, f"referenced evidence is unavailable: {binding.evidence_id}"
            )
        actual = (
            item.query_sha256,
            item.result_sha256,
            item.source_id,
            item.schema_fingerprint,
            item.data_version,
        )
        expected = (
            binding.query_sha256,
            binding.result_sha256,
            binding.source_id,
            binding.schema_fingerprint,
            binding.data_version,
        )
        if actual != expected:
            return TraceVerificationReport(
                False, checked, f"evidence provenance mismatch: {binding.evidence_id}"
            )
        if expected_schema_fingerprints is not None:
            current = expected_schema_fingerprints.get(binding.source_id)
            if current is None or current != binding.schema_fingerprint:
                return TraceVerificationReport(
                    False, checked, f"stale or unknown schema: {binding.source_id}"
                )
        checked += 1

    return TraceVerificationReport(True, checked)
