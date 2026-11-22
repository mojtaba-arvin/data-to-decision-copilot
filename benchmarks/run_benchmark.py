"""Reproducible local latency benchmark for schema, planning, and query-to-evidence."""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path

import duckdb

from d2d.connectors.duckdb import DuckDBConnector
from d2d.core.evidence_store import SQLiteEvidenceStore
from d2d.core.query_planning import QueryPlanningRuntime
from d2d.core.semantic_planning import RuleBasedSemanticPlanner
from d2d.core.sql_executor import DuckDBQueryExecutor


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=100_000)
    parser.add_argument("--iterations", type=int, default=20)
    args = parser.parse_args()
    if args.rows < 1 or args.iterations < 2:
        parser.error("--rows must be positive and --iterations must be at least 2")

    cpu_started = time.process_time()
    with tempfile.TemporaryDirectory(prefix="d2d-benchmark-") as directory:
        root = Path(directory)
        database = root / "benchmark.duckdb"
        connection = duckdb.connect(str(database))
        connection.execute(
            "CREATE TABLE sales AS SELECT i::BIGINT AS id, "
            "CASE WHEN i % 3 = 0 THEN 'north' ELSE 'south' END AS region, "
            "(i % 10000)::DOUBLE / 100 AS amount "
            "FROM range(?) AS rows(i)",
            [args.rows],
        )
        connection.close()

        source = DuckDBConnector(database)
        source.open()
        planner = RuleBasedSemanticPlanner()
        evidence_store = SQLiteEvidenceStore(root / "evidence.sqlite")
        runtime = QueryPlanningRuntime(
            source_id="benchmark-sales",
            data_source=source,
            executor=DuckDBQueryExecutor(database),
            evidence_store=evidence_store,
        )
        try:
            schema_samples: list[float] = []
            plan_samples: list[float] = []
            for _ in range(args.iterations):
                started = time.perf_counter()
                snapshot = source.snapshot()
                schema_samples.append((time.perf_counter() - started) * 1_000)
                started = time.perf_counter()
                planner.plan("count rows in sales", snapshot)
                plan_samples.append((time.perf_counter() - started) * 1_000)

            async def measure() -> list[float]:
                # One warm-up is excluded from the reported sample.
                await runtime.run("count rows in sales", planner)
                samples: list[float] = []
                for _ in range(args.iterations):
                    started = time.perf_counter()
                    result = await runtime.run("count rows in sales", planner)
                    if result.result.rows != ((args.rows,),):
                        raise RuntimeError("benchmark result did not match generated row count")
                    samples.append((time.perf_counter() - started) * 1_000)
                return samples

            end_to_end = asyncio.run(measure())
        finally:
            source.close()

        report = {
            "rows": args.rows,
            "iterations": args.iterations,
            "units": "milliseconds",
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "process_cpu_seconds": round(time.process_time() - cpu_started, 3),
            "duckdb_memory_limit": "256MB",
            "duckdb_threads": 1,
            "schema_discovery": {
                "median": round(statistics.median(schema_samples), 3),
                "p95": round(percentile(schema_samples, 0.95), 3),
            },
            "semantic_planning": {
                "median": round(statistics.median(plan_samples), 3),
                "p95": round(percentile(plan_samples, 0.95), 3),
            },
            "query_to_evidence": {
                "median": round(statistics.median(end_to_end), 3),
                "p95": round(percentile(end_to_end, 0.95), 3),
            },
            "note": "Local regression measurements; not a cross-machine performance claim.",
        }
        print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
