from __future__ import annotations

from pathlib import Path

import duckdb

from d2d.connectors.duckdb import DuckDBConnector
from d2d.core.query_planning import AggregateFunction, AggregateSpec, QueryPlan
from d2d.core.semantic_planning import (
    EvaluationCase,
    RuleBasedSemanticPlanner,
    evaluate_semantic_planner,
)


def test_persian_english_semantic_benchmark_exact_plan_accuracy(tmp_path: Path) -> None:
    database = tmp_path / "benchmark.duckdb"
    connection = duckdb.connect(str(database))
    connection.execute(
        "CREATE TABLE orders (id INTEGER, region VARCHAR, amount DECIMAL(10,2))"
    )
    connection.close()
    with DuckDBConnector(database) as source:
        snapshot = source.snapshot()

    count = QueryPlan(
        schema_name="main", table_name="orders", schema_fingerprint=snapshot.fingerprint,
        aggregates=(AggregateSpec(function=AggregateFunction.COUNT, alias="row_count"),),
        limit=1,
    )
    total = QueryPlan(
        schema_name="main", table_name="orders", schema_fingerprint=snapshot.fingerprint,
        columns=("region",),
        aggregates=(AggregateSpec(function=AggregateFunction.SUM, column="amount", alias="sum_amount"),),
        group_by=("region",), limit=100,
    )
    projection = QueryPlan(
        schema_name="main", table_name="orders", schema_fingerprint=snapshot.fingerprint,
        columns=("id", "region"), limit=100,
    )
    cases = (
        EvaluationCase(question="count rows in orders", expected=count),
        EvaluationCase(question="sum amount by region in orders", expected=total),
        EvaluationCase(question="show id, region from orders", expected=projection),
        EvaluationCase(question="DROP TABLE orders", expected=count),
    )
    report = evaluate_semantic_planner(
        RuleBasedSemanticPlanner(), snapshot=snapshot, cases=cases
    )
    assert report.total == 4
    assert report.passed == 3
    assert report.rejected == 1
    assert report.accuracy == 3 / 4



def test_peak_rss_reports_non_negative_bytes_or_unavailable() -> None:
    from benchmarks.run_benchmark import peak_rss_bytes

    value = peak_rss_bytes()
    assert value is None or value >= 0


def test_sqlite_journal_metrics_reports_records_and_file_size(tmp_path: Path) -> None:
    import sqlite3

    from benchmarks.run_benchmark import sqlite_journal_metrics

    database = tmp_path / "evidence.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE evidence_journal (sequence INTEGER PRIMARY KEY)")
        connection.executemany("INSERT INTO evidence_journal(sequence) VALUES (?)", [(1,), (2,), (3,)])

    report = sqlite_journal_metrics(database)
    assert report["records"] == 3
    assert report["bytes"] == database.stat().st_size
    assert report["bytes"] > 0
