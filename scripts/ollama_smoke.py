"""Real local Ollama smoke: model-generated QueryPlan through guarded execution."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

import duckdb

from d2d.connectors.duckdb import DuckDBConnector
from d2d.core.evidence_store import SQLiteEvidenceStore
from d2d.core.model_planning import ModelBackedQueryPlanner, ModelProviderIdentity
from d2d.core.ollama_provider import OllamaStructuredPlanProvider
from d2d.core.query_planning import QueryPlanningRuntime
from d2d.core.sql_executor import DuckDBQueryExecutor


def main() -> None:
    # This script is invoked by the provider PR's isolated real-inference CI job.
    model = os.environ.get("D2D_OLLAMA_MODEL", "qwen3:8b")
    with tempfile.TemporaryDirectory(prefix="d2d-ollama-smoke-") as directory:
        root = Path(directory)
        database = root / "smoke.duckdb"
        connection = duckdb.connect(str(database))
        connection.execute("CREATE TABLE orders (id INTEGER, amount INTEGER)")
        connection.execute("INSERT INTO orders VALUES (1, 10), (2, 20)")
        connection.close()

        source = DuckDBConnector(database)
        source.open()
        try:
            planner = ModelBackedQueryPlanner(
                provider=OllamaStructuredPlanProvider(
                    model=model, timeout_seconds=180, max_output_tokens=512,
                ),
                provider_identity=ModelProviderIdentity(
                    provider="ollama", model=model, revision=f"ci-smoke-{model}",
                ),
            )
            runtime = QueryPlanningRuntime(
                source_id="ollama-ci-smoke",
                data_source=source,
                executor=DuckDBQueryExecutor(database),
                evidence_store=SQLiteEvidenceStore(root / "evidence.sqlite"),
            )
            result = asyncio.run(runtime.run("count rows in orders", planner))
            if result.result.rows != ((2,),):
                raise AssertionError(f"unexpected real-model query result: {result.result.rows!r}")
            print(json.dumps({
                "status": "passed",
                "model": model,
                "rows": result.result.rows,
                "sql": result.query.sql,
                "evidence_id": result.evidence_id,
                "schema_fingerprint": result.query.schema_fingerprint,
                "planner_reference": result.planner_reference,
            }, default=str))
        finally:
            source.close()


if __name__ == "__main__":
    main()
