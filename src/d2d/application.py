"""Shared application service used by the REST, CLI, and MCP interfaces."""
from __future__ import annotations

import asyncio
from collections import deque
from datetime import UTC, datetime
from threading import Lock
from typing import Any

from d2d.connectors.base import DataSource
from d2d.connectors.duckdb import DuckDBConnector
from d2d.core.evidence_store import SQLiteEvidenceStore
from d2d.core.model_planning import ModelBackedQueryPlanner, ModelProviderIdentity
from d2d.core.ollama_provider import OllamaStructuredPlanProvider
from d2d.core.query_planning import QueryPlanner, QueryPlanningRuntime
from d2d.core.semantic_planning import RuleBasedSemanticPlanner
from d2d.core.sql_executor import DuckDBQueryExecutor


class CopilotService:
    """One bounded query runtime with a small, process-local question history."""

    def __init__(
        self,
        *,
        source: DataSource,
        executor: DuckDBQueryExecutor,
        evidence_store: SQLiteEvidenceStore,
        planner: QueryPlanner,
        source_id: str,
        history_limit: int = 100,
    ) -> None:
        if history_limit < 1:
            raise ValueError("history_limit must be positive")
        self._source = source
        self._planner = planner
        self._runtime = QueryPlanningRuntime(
            source_id=source_id,
            data_source=source,
            executor=executor,
            evidence_store=evidence_store,
        )
        self._history: deque[dict[str, object]] = deque(maxlen=history_limit)
        self._history_lock = Lock()

    async def query(self, question: str) -> dict[str, object]:
        """Execute one natural-language query and return its evidence metadata."""
        result = await self._runtime.run(question, self._planner)
        response: dict[str, object] = {
            "question": question,
            "columns": list(result.result.columns),
            "rows": [[_json_value(value) for value in row] for row in result.result.rows],
            "truncated": result.result.truncated,
            "sql": result.query.sql,
            "parameters": [_json_value(value) for value in result.query.parameters],
            "evidence_id": result.evidence_id,
            "evidence_sequence": result.evidence_sequence,
            "schema_fingerprint": result.query.schema_fingerprint,
            "planner_reference": result.planner_reference,
            "prompt_sha256": result.prompt_sha256,
            "created_at": datetime.now(UTC).isoformat(),
        }
        history_item = {
            key: response[key]
            for key in (
                "question", "evidence_id", "evidence_sequence",
                "schema_fingerprint", "planner_reference", "created_at",
            )
        }
        with self._history_lock:
            self._history.appendleft(history_item)
        return response

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

    def history(self) -> list[dict[str, object]]:
        with self._history_lock:
            return [dict(item) for item in self._history]


def build_service(
    *,
    catalog_path: str,
    evidence_path: str = "d2d-evidence.sqlite",
    provider: str = "rules",
    model: str = "qwen3:8b",
) -> CopilotService:
    """Build the same read-only query pipeline for every product interface."""
    source = DuckDBConnector(catalog_path)
    source.open()
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
        source.close()
        raise ValueError("provider must be 'rules' or 'ollama'")
    return CopilotService(
        source=source,
        executor=DuckDBQueryExecutor(catalog_path),
        evidence_store=SQLiteEvidenceStore(evidence_path),
        planner=planner,
        source_id="analytics-catalog",
    )


def _json_value(value: Any) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return str(value)
