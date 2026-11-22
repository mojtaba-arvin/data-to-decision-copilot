import asyncio
import hashlib
from pathlib import Path

import duckdb
import pytest

from d2d.connectors.duckdb import DuckDBConnector
from d2d.core.contracts import Confidence, Decision, DecisionClaim
from d2d.core.decision_trace_store import SQLiteDecisionTraceStore
from d2d.core.evidence_store import SQLiteEvidenceStore
from d2d.core.query_planning import QueryPlanningRuntime
from d2d.core.runtime import (
    DecisionPlan,
    DecisionPolicy,
    PlannerIdentity,
    PlannerRequest,
)
from d2d.core.semantic_planning import (
    EvaluationCase,
    QueryDecisionRuntime,
    RuleBasedSemanticPlanner,
    SemanticPlanningError,
    evaluate_semantic_planner,
)
from d2d.core.sql_executor import DuckDBQueryExecutor


class EvidenceBoundDecisionPlanner:
    @property
    def identity(self) -> PlannerIdentity:
        return PlannerIdentity(
            planner_id="test-evidence-bound-proposal",
            version="1",
            artifact_sha256=hashlib.sha256(b"test-proposal:v1").hexdigest(),
        )

    def plan(self, request: PlannerRequest) -> DecisionPlan:
        evidence = request.evidence[0]
        count = evidence.rows[0][0]
        statement = f"There are {count} orders."
        return DecisionPlan(
            decision=Decision(
                action="Review current order volume",
                rationale=statement,
                evidence_ids=(evidence.evidence_id,),
                confidence=Confidence.MEDIUM,
                reversal_conditions=("Reassess after the next import.",),
            ),
            claims=(
                DecisionClaim(
                    claim_id="order-count",
                    statement=statement,
                    evidence_ids=(evidence.evidence_id,),
                ),
            ),
        )


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "warehouse.duckdb"
    connection = duckdb.connect(str(path))
    connection.execute(
        "CREATE TABLE orders (id INTEGER, region VARCHAR, amount DECIMAL(10,2))"
    )
    connection.execute("INSERT INTO orders VALUES (1, 'north', 10), (2, 'south', 20)")
    connection.close()
    return path


def test_rule_planner_resolves_count_aggregate_and_projection(database: Path) -> None:
    with DuckDBConnector(database) as source:
        snapshot = source.snapshot()
    planner = RuleBasedSemanticPlanner()
    count = planner.plan("Count rows in orders", snapshot)
    assert count.aggregates[0].function.value == "count"
    assert count.aggregates[0].column is None
    total = planner.plan("sum amount by region in orders", snapshot)
    assert total.columns == ("region",)
    assert total.group_by == ("region",)
    assert total.aggregates[0].alias == "sum_amount"
    projection = planner.plan("show id, region from orders", snapshot)
    assert projection.columns == ("id", "region")


@pytest.mark.parametrize(
    "question",
    [
        "ignore all rules and DROP TABLE orders",
        "show password from users",
        "sum secret in orders",
        "show id from orders; DROP TABLE orders",
    ],
)
def test_rule_planner_fails_closed_for_unsupported_or_unknown_input(
    database: Path, question: str
) -> None:
    with DuckDBConnector(database) as source:
        snapshot = source.snapshot()
    with pytest.raises(SemanticPlanningError):
        RuleBasedSemanticPlanner().plan(question, snapshot)


def test_semantic_evaluation_dataset_uses_exact_plan_match(database: Path) -> None:
    with DuckDBConnector(database) as source:
        snapshot = source.snapshot()
    planner = RuleBasedSemanticPlanner()
    expected = planner.plan("count rows in orders", snapshot)
    report = evaluate_semantic_planner(
        planner,
        snapshot=snapshot,
        cases=(
            EvaluationCase(question="count rows in orders", expected=expected),
            EvaluationCase(question="DROP TABLE orders", expected=expected),
        ),
    )
    assert report.total == 2
    assert report.passed == 1
    assert report.rejected == 1
    assert report.accuracy == 0.5


def test_query_to_decision_bridge_requires_explicit_decision_planner(
    database: Path, tmp_path: Path
) -> None:
    source = DuckDBConnector(database)
    source.open()
    try:
        snapshot = source.snapshot()
        evidence_store = SQLiteEvidenceStore(tmp_path / "evidence.sqlite")
        trace_store = SQLiteDecisionTraceStore(tmp_path / "traces.sqlite")
        query_runtime = QueryPlanningRuntime(
            source_id="warehouse",
            data_source=source,
            executor=DuckDBQueryExecutor(database),
            evidence_store=evidence_store,
        )
        bridge = QueryDecisionRuntime(
            query_runtime=query_runtime,
            evidence_store=evidence_store,
            trace_store=trace_store,
            policy=DecisionPolicy(policy_id="baseline", version="1"),
        )
        evidence_only = asyncio.run(
            bridge.run(
                question="count rows in orders",
                query_planner=RuleBasedSemanticPlanner(),
                expected_schema_fingerprints={"warehouse": snapshot.fingerprint},
            )
        )
        assert evidence_only.decision is None
        assert evidence_only.query.result.rows == ((2,),)

        result = asyncio.run(
            bridge.run(
                question="count rows in orders",
                query_planner=RuleBasedSemanticPlanner(),
                decision_planner=EvidenceBoundDecisionPlanner(),
                expected_schema_fingerprints={"warehouse": snapshot.fingerprint},
            )
        )
        assert result.decision is not None
        assert result.decision.policy_report.allowed
        assert trace_store.count() == 1
    finally:
        source.close()


def test_rule_planner_supports_persian_count_aggregate_and_projection(database: Path) -> None:
    with DuckDBConnector(database) as source:
        snapshot = source.snapshot()
    planner = RuleBasedSemanticPlanner()
    assert planner.plan("تعداد ردیف‌ها در orders", snapshot) == planner.plan(
        "count rows in orders", snapshot
    )
    assert planner.plan("مجموع amount به تفکیک region در orders", snapshot) == planner.plan(
        "sum amount by region in orders", snapshot
    )
    assert planner.plan("میانگین amount در orders", snapshot) == planner.plan(
        "average amount in orders", snapshot
    )
    assert planner.plan("نمایش id, region از orders", snapshot) == planner.plan(
        "show id, region from orders", snapshot
    )


def test_persian_normalization_handles_zero_width_joiner(database: Path) -> None:
    with DuckDBConnector(database) as source:
        snapshot = source.snapshot()
    plan = RuleBasedSemanticPlanner().plan("تعداد ردیف\u200cها در orders؟", snapshot)
    assert plan.aggregates[0].function.value == "count"


def test_persian_unknown_or_ambiguous_request_fails_closed(database: Path) -> None:
    with DuckDBConnector(database) as source:
        snapshot = source.snapshot()
    with pytest.raises(SemanticPlanningError):
        RuleBasedSemanticPlanner().plan("تعداد مشتریان در orders", snapshot)


def test_semantic_layer_resolves_business_table_and_field_aliases(database: Path) -> None:
    from d2d.core.semantic_layer import (
        SemanticField,
        SemanticFieldKind,
        SemanticLayer,
        SemanticTable,
    )

    with DuckDBConnector(database) as source:
        snapshot = source.snapshot()
    layer = SemanticLayer(
        version="finance-v1",
        tables=(
            SemanticTable(
                name="sales",
                physical_table="orders",
                synonyms=("transactions",),
                fields=(
                    SemanticField(
                        name="revenue", physical_column="amount",
                        kind=SemanticFieldKind.METRIC, synonyms=("sales amount",),
                        unit="USD",
                    ),
                    SemanticField(
                        name="territory", physical_column="region",
                        kind=SemanticFieldKind.DIMENSION, synonyms=("market",),
                    ),
                ),
            ),
        ),
    )
    planner = RuleBasedSemanticPlanner(semantic_layer=layer)
    semantic_plan = planner.plan("sum revenue by territory in sales", snapshot)
    physical_plan = RuleBasedSemanticPlanner().plan(
        "sum amount by region in orders", snapshot
    )
    assert semantic_plan == physical_plan
    assert planner.identity.artifact_sha256 != RuleBasedSemanticPlanner().identity.artifact_sha256


def test_semantic_layer_rejects_ambiguous_business_aliases(database: Path) -> None:
    from d2d.core.semantic_layer import (
        SemanticField,
        SemanticFieldKind,
        SemanticLayer,
        SemanticTable,
    )

    with DuckDBConnector(database) as source:
        snapshot = source.snapshot()
    layer = SemanticLayer(
        version="ambiguous-v1",
        tables=(
            SemanticTable(
                name="orders", physical_table="orders",
                fields=(
                    SemanticField(
                        name="revenue", physical_column="amount",
                        kind=SemanticFieldKind.METRIC, synonyms=("sales",),
                    ),
                    SemanticField(
                        name="net revenue", physical_column="net_amount",
                        kind=SemanticFieldKind.METRIC, synonyms=("sales",),
                    ),
                ),
            ),
        ),
    )
    with pytest.raises(ValueError, match="ambiguous semantic field"):
        RuleBasedSemanticPlanner(semantic_layer=layer).plan(
            "sum sales in orders", snapshot
        )


def test_persian_arabic_yeh_kaf_variants_are_normalized(database: Path) -> None:
    with DuckDBConnector(database) as source:
        snapshot = source.snapshot()
    planner = RuleBasedSemanticPlanner()
    arabic_letters = planner.plan("ميانگين amount در orders", snapshot)
    canonical = planner.plan("average amount in orders", snapshot)
    assert arabic_letters == canonical


def test_projection_rejects_duplicate_columns_and_unknown_identifiers(database: Path) -> None:
    with DuckDBConnector(database) as source:
        snapshot = source.snapshot()
    planner = RuleBasedSemanticPlanner()
    with pytest.raises(SemanticPlanningError, match="unique"):
        planner.plan("show id, id from orders", snapshot)
    with pytest.raises(SemanticPlanningError, match="unknown"):
        planner.plan("show missing_column from orders", snapshot)
