import json
from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest

from d2d.connectors.base import SchemaSnapshot
from d2d.connectors.duckdb import DuckDBConnector
from d2d.core.model_planning import (
    ModelBackedQueryPlanner,
    ModelPlanningError,
    ModelProviderIdentity,
    StructuredGenerationRequest,
)
from d2d.core.query_planning import QueryPlan, QueryPlanningError, compile_query_plan


class FakeStructuredProvider:
    def __init__(self, response: str) -> None:
        self.response = response
        self.requests: list[StructuredGenerationRequest] = []

    def generate(self, request: StructuredGenerationRequest) -> str:
        self.requests.append(request)
        return self.response


@pytest.fixture
def snapshot(tmp_path: Path) -> Iterator[SchemaSnapshot]:
    path = tmp_path / "model-planner.duckdb"
    connection = duckdb.connect(str(path))
    connection.execute(
        "CREATE TABLE orders (id INTEGER, region VARCHAR, amount DECIMAL(10,2))"
    )
    connection.close()
    with DuckDBConnector(path) as source:
        yield source.snapshot()


def make_plan(snapshot: SchemaSnapshot, **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_name": "main",
        "table_name": "orders",
        "schema_fingerprint": snapshot.fingerprint,
        "columns": ["region"],
        "aggregates": [
            {"function": "sum", "column": "amount", "alias": "total"}
        ],
        "group_by": ["region"],
        "limit": 25,
    }
    value.update(overrides)
    return value


def planner(response: str) -> tuple[ModelBackedQueryPlanner, FakeStructuredProvider]:
    provider = FakeStructuredProvider(response)
    return (
        ModelBackedQueryPlanner(
            provider=provider,
            provider_identity=ModelProviderIdentity(
                provider="test-provider", model="test-model", revision="release-1"
            ),
        ),
        provider,
    )


def test_model_output_becomes_only_a_typed_plan(snapshot: SchemaSnapshot) -> None:
    raw = json.dumps(make_plan(snapshot))
    adapter, provider = planner(raw)
    result = adapter.plan("total amount by region in orders", snapshot)

    assert isinstance(result, QueryPlan)
    assert result.table_name == "orders"
    assert result.aggregates[0].column == "amount"
    assert provider.requests[0].max_output_bytes == 65_536
    assert "DROP TABLE" not in provider.requests[0].system_prompt
    assert snapshot.fingerprint in provider.requests[0].user_prompt
    assert provider.requests[0].response_schema["title"] == "QueryPlan"


@pytest.mark.parametrize(
    "response",
    [
        "",
        "Here is your SQL: SELECT * FROM orders",
        '{"schema_name":"main","schema_name":"evil"}',
        '{"schema_name":"main","table_name":"orders","schema_fingerprint":"'
        + "0" * 64
        + '","columns":["region"],"sql":"DROP TABLE orders"}',
        '{"schema_name":"main","table_name":"orders","schema_fingerprint":"'
        + "0" * 64
        + '","columns":["region"],"limit":NaN}',
    ],
)
def test_rejects_non_json_duplicate_keys_extra_sql_and_non_finite_values(
    snapshot: SchemaSnapshot, response: str
) -> None:
    adapter, _ = planner(response)
    with pytest.raises(ModelPlanningError):
        adapter.plan("show region from orders", snapshot)


def test_rejects_schema_fingerprint_mismatch(snapshot: SchemaSnapshot) -> None:
    adapter, _ = planner(json.dumps(make_plan(snapshot, schema_fingerprint="0" * 64)))
    with pytest.raises(ModelPlanningError, match="fingerprint"):
        adapter.plan("show region from orders", snapshot)


def test_rejects_unknown_column_before_runtime_compilation(snapshot: SchemaSnapshot) -> None:
    adapter, _ = planner(json.dumps(make_plan(snapshot, columns=["secret"])))
    # The existing compiler remains authoritative for identifier validation.
    result = adapter.plan("show secret from orders", snapshot)
    with pytest.raises(QueryPlanningError, match="unknown columns"):
        compile_query_plan(result, snapshot=snapshot, source_id="test")


def test_rejects_oversized_provider_output(snapshot: SchemaSnapshot) -> None:
    adapter, _ = planner(" " * 65_537)
    with pytest.raises(ModelPlanningError, match="byte limit"):
        adapter.plan("show region from orders", snapshot)


def test_rejects_oversized_question_before_provider_call(snapshot: SchemaSnapshot) -> None:
    adapter, provider = planner("{}")
    with pytest.raises(ModelPlanningError, match="question"):
        adapter.plan("x" * 8_193, snapshot)
    assert provider.requests == []


def test_planner_identity_is_stable_and_release_specific() -> None:
    first, _ = planner("{}")
    second, _ = planner("{}")
    assert first.identity == second.identity
    assert len(first.identity.artifact_sha256) == 64
    assert len(first.identity.prompt_sha256) == 64
    changed = ModelBackedQueryPlanner(
        provider=FakeStructuredProvider("{}"),
        provider_identity=ModelProviderIdentity(
            provider="test-provider", model="test-model", revision="release-2"
        ),
    )
    assert changed.identity.artifact_sha256 != first.identity.artifact_sha256
