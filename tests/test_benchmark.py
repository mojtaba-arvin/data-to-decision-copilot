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
        EvaluationCase(question="تعداد ردیف‌ها در orders", expected=count),
        EvaluationCase(question="sum amount by region in orders", expected=total),
        EvaluationCase(question="مجموع amount به تفکیک region در orders", expected=total),
        EvaluationCase(question="show id, region from orders", expected=projection),
        EvaluationCase(question="نمایش id, region از orders", expected=projection),
        EvaluationCase(question="DROP TABLE orders", expected=count),
    )
    report = evaluate_semantic_planner(
        RuleBasedSemanticPlanner(), snapshot=snapshot, cases=cases
    )
    assert report.total == 7
    assert report.passed == 6
    assert report.rejected == 1
    assert report.accuracy == 6 / 7
