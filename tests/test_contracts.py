import asyncio
import hashlib
from datetime import UTC

import pytest
from pydantic import ValidationError

from d2d.core.contracts import (
    Confidence,
    CopilotResponse,
    Decision,
    Evidence,
    EvidenceStatus,
)
from d2d.core.evidence import create_execution_evidence
from d2d.core.sql_executor import DuckDBQueryExecutor, ExecutionLimits, QueryResult


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
            status=EvidenceStatus.EXECUTED, row_count=2, rows=((1,),),
        )


def test_unexecuted_evidence_cannot_claim_result_rows() -> None:
    with pytest.raises(ValidationError, match="unexecuted evidence"):
        Evidence(
            evidence_id="q1", sql="SELECT 1", dialect="sqlite",
            status=EvidenceStatus.NOT_EXECUTED, row_count=1, rows=((1,),),
        )


def test_factory_builds_hash_linked_immutable_evidence_from_executor() -> None:
    async def scenario() -> None:
        result = await DuckDBQueryExecutor(
            limits=ExecutionLimits(max_rows=2, max_bytes=1024)
        ).execute("SELECT i FROM range(3) AS t(i)")
        evidence = create_execution_evidence(
            sql="SELECT i FROM range(3) AS t(i)",
            dialect="duckdb",
            result=result,
            source_id="warehouse-prod-readonly",
            schema_fingerprint="a" * 64,
            execution_ms=12.5,
            data_version="snapshot-2026-11-22",
        )
        assert evidence.status is EvidenceStatus.EXECUTED
        assert evidence.row_count == 2
        assert evidence.truncated is True
        assert evidence.executed_at is not None
        assert evidence.executed_at.tzinfo == UTC
        assert evidence.query_sha256 == hashlib.sha256(
            evidence.sql.encode("utf-8")
        ).hexdigest()
        assert len(evidence.result_sha256 or "") == 64
        with pytest.raises(ValidationError):
            evidence.sql = "SELECT 999"

    asyncio.run(scenario())


def test_evidence_hash_changes_when_result_changes() -> None:
    def build(value: int) -> Evidence:
        return create_execution_evidence(
            sql="SELECT 1 AS value",
            dialect="duckdb",
            result=QueryResult(
                columns=("value",), rows=((value,),), captured_rows=1,
                output_bytes=20, truncated=False,
            ),
            source_id="local-db",
            schema_fingerprint="b" * 64,
            execution_ms=1,
        )

    assert build(1).result_sha256 != build(2).result_sha256


def test_factory_rejects_mismatched_executor_row_count() -> None:
    with pytest.raises(ValueError, match="captured_rows"):
        create_execution_evidence(
            sql="SELECT 1",
            dialect="duckdb",
            result=QueryResult(
                columns=("1",), rows=((1,),), captured_rows=0,
                output_bytes=10, truncated=False,
            ),
            source_id="local-db",
            schema_fingerprint="c" * 64,
            execution_ms=1,
        )
