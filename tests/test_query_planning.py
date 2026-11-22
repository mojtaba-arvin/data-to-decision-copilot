from pathlib import Path

import asyncio
import duckdb
import pytest

from d2d.connectors.duckdb import DuckDBConnector
from d2d.connectors.base import SchemaSnapshot
from d2d.core.evidence_store import SQLiteEvidenceStore
from d2d.core.query_planning import (
    AggregateFunction,
    AggregateSpec,
    DeterministicQueryPlanner,
    FilterOperator,
    QueryFilter,
    QueryPlan,
    QueryPlanningError,
    QueryPlanningRuntime,
    QuerySort,
    SortDirection,
    compile_query_plan,
)
from d2d.core.sql_executor import DuckDBQueryExecutor


@pytest.fixture
def source_db(tmp_path: Path) -> Path:
    path = tmp_path / "sales.duckdb"
    connection = duckdb.connect(str(path))
    connection.execute(
        "CREATE TABLE orders (id INTEGER, region VARCHAR, amount DECIMAL(10,2))"
    )
    connection.execute(
        "INSERT INTO orders VALUES (1, 'north', 10), (2, 'north', 20), (3, 'south', 30)"
    )
    connection.close()
    return path


def make_plan(snapshot: SchemaSnapshot, **overrides: object) -> QueryPlan:
    values: dict[str, object] = {
        "schema_name": "main",
        "table_name": "orders",
        "schema_fingerprint": snapshot.fingerprint,
        "columns": ("region",),
        "aggregates": (
            AggregateSpec(function=AggregateFunction.SUM, column="amount", alias="total"),
        ),
        "group_by": ("region",),
        "filters": (QueryFilter(column="amount", operator=FilterOperator.GT, value=5),),
        "order_by": (QuerySort(output_name="total", direction=SortDirection.DESC),),
        "limit": 10,
    }
    values.update(overrides)
    return QueryPlan.model_validate(values)


def test_compiles_schema_grounded_plan_with_bound_parameters(source_db: Path) -> None:
    with DuckDBConnector(source_db) as source:
        snapshot = source.snapshot()
    compiled = compile_query_plan(make_plan(snapshot), snapshot=snapshot, source_id="sales")
    assert '"main"."orders"' in compiled.sql
    assert "SUM(" in compiled.sql
    assert compiled.parameters == (5, 10)
    assert "5" not in compiled.sql


@pytest.mark.parametrize("field", ["missing", 'region" FROM orders; DROP TABLE orders --'])
def test_rejects_unknown_or_injected_column(source_db: Path, field: str) -> None:
    with DuckDBConnector(source_db) as source:
        snapshot = source.snapshot()
    with pytest.raises(QueryPlanningError, match="unknown columns"):
        compile_query_plan(
            make_plan(snapshot, columns=(field,), aggregates=(), group_by=()),
            snapshot=snapshot,
            source_id="sales",
        )


def test_rejects_unknown_table(source_db: Path) -> None:
    with DuckDBConnector(source_db) as source:
        snapshot = source.snapshot()
    with pytest.raises(QueryPlanningError, match="table is not present"):
        compile_query_plan(
            make_plan(snapshot, table_name="secrets"),
            snapshot=snapshot,
            source_id="sales",
        )


def test_rejects_stale_schema_fingerprint(source_db: Path) -> None:
    with DuckDBConnector(source_db) as source:
        snapshot = source.snapshot()
    with pytest.raises(QueryPlanningError, match="fingerprint is stale"):
        compile_query_plan(
            make_plan(snapshot, schema_fingerprint="0" * 64),
            snapshot=snapshot,
            source_id="sales",
        )


def test_limit_is_hard_bounded() -> None:
    with pytest.raises(ValueError):
        QueryPlan(
            schema_name="main", table_name="orders", schema_fingerprint="a" * 64,
            columns=("id",), limit=1001,
        )


def test_query_plan_rejects_aggregate_without_grouping() -> None:
    with pytest.raises(ValueError, match="GROUP BY"):
        QueryPlan(
            schema_name="main", table_name="orders", schema_fingerprint="a" * 64,
            columns=("region",),
            aggregates=(AggregateSpec(function=AggregateFunction.SUM, column="amount", alias="total"),),
        )


def test_query_plan_rejects_empty_in_filter() -> None:
    with pytest.raises(ValueError, match="non-empty list"):
        QueryFilter(column="id", operator=FilterOperator.IN, value=[])


def test_query_plan_runtime_executes_and_persists_real_evidence(
    source_db: Path, tmp_path: Path
) -> None:
    async def scenario() -> None:
        source = DuckDBConnector(source_db)
        source.open()
        try:
            snapshot = source.snapshot()
            plan = make_plan(snapshot)
            store = SQLiteEvidenceStore(tmp_path / "evidence.sqlite")
            runtime = QueryPlanningRuntime(
                source_id="sales",
                data_source=source,
                executor=DuckDBQueryExecutor(source_db),
                evidence_store=store,
            )
            outcome = await runtime.run(
                "Which region has the highest total sales?",
                DeterministicQueryPlanner(plan),
            )
            assert outcome.result.columns == ("region", "total")
            assert outcome.result.rows == (("north", 30), ("south", 30))
            assert outcome.evidence_sequence == 1
            saved = store.get(outcome.evidence_id)
            assert saved is not None
            assert saved.rows == outcome.result.rows
            assert saved.sql == outcome.query.sql
            assert saved.schema_fingerprint == snapshot.fingerprint
            assert store.verify().valid
            assert outcome.planner_reference.startswith("deterministic-query-plan@1#sha256:")
            assert len(outcome.prompt_sha256) == 64
            assert saved.execution_ms is not None and saved.execution_ms > 0
        finally:
            source.close()
    asyncio.run(scenario())


def test_multiple_filters_are_bound_and_null_filter_is_structural(source_db: Path) -> None:
    with DuckDBConnector(source_db) as source:
        snapshot = source.snapshot()
    plan = make_plan(
        snapshot,
        aggregates=(),
        columns=("id", "region"),
        group_by=(),
        filters=(
            QueryFilter(column="region", operator=FilterOperator.IN, value=["north", "south"]),
            QueryFilter(column="amount", operator=FilterOperator.GTE, value=10),
        ),
        order_by=(),
    )
    compiled = compile_query_plan(plan, snapshot=snapshot, source_id="sales")
    assert compiled.parameters == ("north", "south", 10, 10)
    assert "IN (?, ?)" in compiled.sql


def test_planner_cannot_supply_raw_sql_or_extra_fields(source_db: Path) -> None:
    with DuckDBConnector(source_db) as source:
        snapshot = source.snapshot()
    with pytest.raises(ValueError):
        QueryPlan.model_validate({
            "schema_name": "main", "table_name": "orders",
            "schema_fingerprint": snapshot.fingerprint,
            "columns": ("id",), "sql": "DROP TABLE orders",
        })
