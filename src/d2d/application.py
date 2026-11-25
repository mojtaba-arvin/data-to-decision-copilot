"""Shared application service used by the REST, CLI, and MCP interfaces."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from d2d.connectors.base import DataSource
from d2d.connectors.duckdb import DuckDBConnector
from d2d.core.contracts import Confidence, Decision, DecisionClaim
from d2d.core.decision_trace_store import SQLiteDecisionTraceStore
from d2d.core.evidence_store import SQLiteEvidenceStore
from d2d.core.isolated_query_executor import IsolatedDuckDBQueryExecutor
from d2d.core.model_planning import ModelBackedQueryPlanner, ModelProviderIdentity
from d2d.core.ollama_provider import OllamaStructuredPlanProvider
from d2d.core.query_planning import QueryExecutor, QueryPlanner, QueryPlanningRuntime
from d2d.core.runtime import (
    DecisionPlan,
    DecisionPolicy,
    DecisionRuntime,
    DeterministicPlanner,
)
from d2d.core.semantic_planning import RuleBasedSemanticPlanner
from d2d.core.sql_executor import DuckDBQueryExecutor
from d2d.query_history import QueryHistoryItem, SQLiteQueryHistoryStore


class CopilotService:
    """One bounded query runtime with a durable, operator-configurable question history."""

    def __init__(
        self,
        *,
        source: DataSource,
        executor: QueryExecutor,
        evidence_store: SQLiteEvidenceStore,
        planner: QueryPlanner,
        source_id: str,
        history_store: SQLiteQueryHistoryStore,
        trace_store: SQLiteDecisionTraceStore,
    ) -> None:
        self._source = source
        self._source_id = source_id
        self._history_store = history_store
        self._evidence_store = evidence_store
        self._trace_store = trace_store
        self._planner = planner
        self._runtime = QueryPlanningRuntime(
            source_id=source_id,
            data_source=source,
            executor=executor,
            evidence_store=evidence_store,
        )

    async def query(self, question: str) -> dict[str, object]:
        """Execute one natural-language query and return its evidence metadata."""
        result = await self._runtime.run(question, self._planner)
        response: dict[str, object] = {
            "question": question,
            "columns": list(result.result.columns),
            "rows": [[_json_value(value) for value in row] for row in result.result.rows],
            "truncated": result.result.truncated,
            "sql": result.query.sql,
            "parameter_count": len(result.query.parameters),
            "evidence_id": result.evidence_id,
            "evidence_sequence": result.evidence_sequence,
            "schema_fingerprint": result.query.schema_fingerprint,
            "planner_reference": result.planner_reference,
            "prompt_sha256": result.prompt_sha256,
            "created_at": datetime.now(UTC).isoformat(),
        }
        self._history_store.append(
            QueryHistoryItem(
                question=question,
                evidence_id=result.evidence_id,
                evidence_sequence=result.evidence_sequence,
                schema_fingerprint=result.query.schema_fingerprint,
                planner_reference=result.planner_reference,
                created_at=str(response["created_at"]),
            )
        )
        return response

    def decide(
        self, *, question: str, evidence_id: str,
        decision: dict[str, object], claims: list[dict[str, object]],
    ) -> dict[str, object]:
        """Persist a human-authored proposal only after evidence and policy verification.

        This endpoint deliberately does not synthesize recommendations from query rows.
        The caller supplies the proposal; the runtime verifies its evidence references,
        current schema provenance, numeric fact grounding, and fail-closed policy.
        """
        from pydantic import ValidationError

        if not question.strip() or not evidence_id.strip():
            raise ValueError("question and evidence_id must not be empty")
        history_limit = (
            min(1_000, self._history_store.max_records)
            if self._history_store.max_records is not None
            else 1_000
        )
        if not any(
            item.evidence_id == evidence_id and item.question == question
            for item in self._history_store.list(limit=history_limit)
        ):
            raise ValueError("evidence_id is not associated with this exact query in local history")
        try:
            proposal = Decision.model_validate(decision)
            claim_items = tuple(DecisionClaim.model_validate(item) for item in claims)
            plan = DecisionPlan(decision=proposal, claims=claim_items)
        except (ValidationError, TypeError) as exc:
            raise ValueError("invalid decision proposal") from exc
        if proposal.evidence_ids != (evidence_id,):
            raise ValueError("proposal must reference exactly the submitted evidence_id")
        policy = DecisionPolicy(
            policy_id="human-authored-proposal",
            version="1",
            allowed_confidence=(Confidence.LOW, Confidence.MEDIUM),
            reject_truncated_evidence=True,
            require_reversal_conditions=True,
            require_alternatives=True,
        )
        runtime = DecisionRuntime(
            evidence_store=self._evidence_store,
            trace_store=self._trace_store,
            policy=policy,
        )
        result = runtime.run(
            question=question,
            evidence_ids=(evidence_id,),
            planner=DeterministicPlanner(plan_template=plan),
            expected_schema_fingerprints={
                self._source_id: self._source.snapshot().fingerprint,
            },
        )
        return {
            "trace": result.trace.model_dump(mode="json"),
            "sequence": result.sequence,
            "policy": {
                "reference": result.policy_report.policy_reference,
                "allowed": result.policy_report.allowed,
                "violations": list(result.policy_report.violations),
            },
            "proposal_origin": "caller_supplied",
        }

    def schema(self) -> dict[str, object]:
        snapshot = self._source.snapshot()
        return {
            "fingerprint": snapshot.fingerprint,
            "tables": [
                {
                    "catalog": table.table.catalog,
                    "schema": table.table.schema,
                    "name": table.table.name,
                    "type": table.table.table_type,
                    "columns": [
                        {
                            "name": column.name,
                            "type": column.data_type,
                            "nullable": column.nullable,
                        }
                        for column in table.columns
                    ],
                }
                for table in snapshot.tables
            ],
        }

    def history(self, *, limit: int = 100) -> list[dict[str, object]]:
        retention_limit = self._history_store.max_records
        effective_limit = min(limit, retention_limit) if retention_limit is not None else limit
        return [item.as_dict() for item in self._history_store.list(limit=effective_limit)]

    def traces(self, *, limit: int = 100) -> list[dict[str, object]]:
        """Return verified trace summaries; an empty list means no decisions were traced."""
        return [
            {
                "trace_id": trace.trace_id,
                "question": trace.question,
                "action": trace.decision.action,
                "confidence": trace.decision.confidence.value,
                "created_at": trace.created_at.isoformat(),
                "evidence_count": len(trace.evidence),
                "trace_sha256": trace.trace_sha256,
            }
            for trace in self._trace_store.list(
                limit=limit, evidence_store=self._evidence_store
            )
        ]

    def trace(self, trace_id: str) -> dict[str, object] | None:
        """Retrieve a trace only after verifying its journal and linked evidence."""
        try:
            trace = self._trace_store.replay(
                trace_id, evidence_store=self._evidence_store
            )
        except KeyError:
            return None
        return trace.model_dump(mode="json")

    def readiness(self) -> dict[str, object]:
        """Report whether the catalog and durable journals can serve requests.

        This is a lightweight dependency check, distinct from process liveness.
        It deliberately avoids full hash-chain scans on every probe; integrity
        verification belongs in explicit audit, backup, and restore operations.
        Details are categorical and do not expose paths or payloads.
        """
        checks: dict[str, str] = {}
        try:
            self._source.snapshot()
            checks["catalog"] = "ok"
        except Exception:
            checks["catalog"] = "unavailable"
        try:
            self._evidence_store.count()
            checks["evidence_journal"] = "ok"
        except Exception:
            checks["evidence_journal"] = "unavailable"
        try:
            self._trace_store.count()
            checks["decision_trace_journal"] = "ok"
        except Exception:
            checks["decision_trace_journal"] = "unavailable"
        if (
            isinstance(self._planner, ModelBackedQueryPlanner)
            and isinstance(self._planner.provider, OllamaStructuredPlanProvider)
        ):
            checks["model_provider"] = (
                "ok" if self._planner.provider.is_ready() else "unavailable"
            )
        ready = all(value == "ok" for value in checks.values())
        return {"status": "ready" if ready else "not_ready", "checks": checks}

    def close(self) -> None:
        """Close the owned DuckDB catalog connection."""
        if isinstance(self._source, DuckDBConnector):
            self._source.close()
        self._history_store.close()


def build_service(
    *,
    catalog_path: str,
    evidence_path: str = "d2d-evidence.sqlite",
    history_path: str = "d2d-history.sqlite",
    history_max_records: int | None = None,
    trace_path: str = "d2d-traces.sqlite",
    provider: str = "rules",
    model: str = "qwen3:8b",
    isolated_worker: bool = False,
) -> CopilotService:
    """Build the same read-only query pipeline for every product interface."""
    if history_max_records is not None and history_max_records < 1:
        raise ValueError("history_max_records must be positive when configured")
    source = DuckDBConnector(catalog_path)
    source.open()
    history_store: SQLiteQueryHistoryStore | None = None
    try:
        if provider == "rules":
            planner: QueryPlanner = RuleBasedSemanticPlanner()
        elif provider == "ollama":
            planner = ModelBackedQueryPlanner(
                provider=OllamaStructuredPlanProvider(model=model),
                provider_identity=ModelProviderIdentity(
                    provider="ollama", model=model, revision=f"local-tag-{model}",
                ),
            )
        else:
            raise ValueError("provider must be 'rules' or 'ollama'")
        evidence_store = SQLiteEvidenceStore(evidence_path)
        history_store = SQLiteQueryHistoryStore(history_path, max_records=history_max_records)
        trace_store = SQLiteDecisionTraceStore(trace_path)
        return CopilotService(
            source=source,
            executor=(
                IsolatedDuckDBQueryExecutor(catalog_path)
                if isolated_worker
                else DuckDBQueryExecutor(catalog_path)
            ),
            evidence_store=evidence_store,
            planner=planner,
            source_id="analytics-catalog",
            history_store=history_store,
            trace_store=trace_store,
        )
    except BaseException:
        if history_store is not None:
            history_store.close()
        source.close()
        raise


def _json_value(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, tuple):
        return [_json_value(item) for item in cast(tuple[object, ...], value)]
    if isinstance(value, list):
        return [_json_value(item) for item in cast(list[object], value)]
    return str(value)
