"""Constrained bilingual semantic planning and query-to-decision orchestration."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from d2d.connectors.base import SchemaSnapshot, TableSchema
from d2d.core.decision_trace_store import SQLiteDecisionTraceStore
from d2d.core.evidence_store import SQLiteEvidenceStore
from d2d.core.query_planning import (
    AggregateFunction,
    AggregateSpec,
    QueryPlan,
    QueryPlanner,
    QueryPlannerIdentity,
    QueryPlanningResult,
    QueryPlanningRuntime,
)
from d2d.core.runtime import DecisionPolicy, DecisionRuntime, RuntimeResult
from d2d.core.runtime import Planner as DecisionPlanner
from d2d.core.semantic_layer import SemanticLayer


class SemanticPlanningError(ValueError):
    """Raised when the supported question grammar cannot be resolved safely."""


def _normalize_question(question: str) -> str:
    """Translate a small explicit Persian phrase grammar; identifiers stay schema-grounded."""
    text = question.strip().replace("ي", "ی").replace("ك", "ک").replace("\u200c", "")
    text = " ".join(text.split()).rstrip("؟?!.")

    patterns = (
        (r"^تعداد\s+(?:ردیف(?:ها)?|رکورد(?:ها)?)\s+(?:در|از)\s+", "count rows in "),
        (r"^شمارش\s+(?:ردیف(?:ها)?|رکورد(?:ها)?)\s+(?:در|از)\s+", "count rows in "),
        (r"^(?:مجموع|جمع)\s+(.+?)\s+به\s+تفکیک\s+(.+?)\s+(?:در|از)\s+", r"sum \1 by \2 in "),
        (r"^(?:میانگین|متوسط)\s+(.+?)\s+(?:در|از)\s+", r"average \1 in "),
        (r"^(?:کمینه|حداقل)\s+(.+?)\s+(?:در|از)\s+", r"min \1 in "),
        (r"^(?:بیشینه|حداکثر)\s+(.+?)\s+(?:در|از)\s+", r"max \1 in "),
        (r"^(?:نمایش|نشان\s+بده|فهرست)\s+(.+?)\s+(?:از|در)\s+", r"show \1 from "),
    )
    for pattern, replacement in patterns:
        translated, count = re.subn(pattern, replacement, text, count=1, flags=re.IGNORECASE)
        if count:
            text = translated
            break
    return " ".join(text.split())


@dataclass(frozen=True, slots=True)
class RuleBasedSemanticPlanner:
    """Offline deterministic parser for explicit Persian/English aggregate/projection templates.

    Persian examples: «تعداد ردیف‌ها در orders», «مجموع amount به تفکیک region در orders»,
    «میانگین amount در orders», «نمایش id, region از orders».
    Table and column names must exist in the discovered schema. No fuzzy matching,
    inferred joins, filters, or free-form SQL are accepted.
    """
    planner_id: str = "offline-bilingual-rule-semantic-planner"
    version: str = "3"
    semantic_layer: SemanticLayer | None = None

    @property
    def identity(self) -> QueryPlannerIdentity:
        semantic_definition = (
            self.semantic_layer.model_dump_json() if self.semantic_layer is not None else "no-semantic-layer"
        )
        artifact = hashlib.sha256(
            (
                "offline-bilingual-rule-semantic-planner:v3:fa-en:count,sum,avg,min,max,projection:"
                + semantic_definition
            ).encode("utf-8")
        ).hexdigest()
        prompt = hashlib.sha256(
            b"grammar:fa-en:count|aggregate-column-by-group|show-columns"
        ).hexdigest()
        return QueryPlannerIdentity(
            planner_id=self.planner_id, version=self.version,
            artifact_sha256=artifact, prompt_sha256=prompt,
        )

    def plan(self, question: str, snapshot: SchemaSnapshot) -> QueryPlan:
        text = _normalize_question(question)
        if not text:
            raise SemanticPlanningError("question must not be empty")
        count = re.fullmatch(
            r"count\s+(?:of\s+)?(?:rows|records)\s+(?:in|from)\s+([\w]+)",
            text, re.IGNORECASE,
        )
        aggregate = re.fullmatch(
            r"(sum|total|average|avg|min|max)\s+(?:of\s+)?([\w]+)"
            r"(?:\s+by\s+([\w]+))?\s+(?:in|from)\s+([\w]+)",
            text, re.IGNORECASE,
        )
        projection = re.fullmatch(
            r"(?:show|list|select)\s+([\w, ]+)\s+(?:in|from)\s+([\w]+)",
            text, re.IGNORECASE,
        )
        if count:
            table = self._table(snapshot, count.group(1))
            return QueryPlan(
                schema_name=table.table.schema, table_name=table.table.name,
                schema_fingerprint=snapshot.fingerprint,
                aggregates=(AggregateSpec(function=AggregateFunction.COUNT, alias="row_count"),),
                limit=1,
            )
        if aggregate:
            operation, column_name, group_name, table_name = aggregate.groups()
            table = self._table(snapshot, table_name)
            column = self._column(table, column_name)
            function = {
                "sum": AggregateFunction.SUM, "total": AggregateFunction.SUM,
                "average": AggregateFunction.AVG, "avg": AggregateFunction.AVG,
                "min": AggregateFunction.MIN, "max": AggregateFunction.MAX,
            }[operation.casefold()]
            groups = (self._column(table, group_name),) if group_name else ()
            return QueryPlan(
                schema_name=table.table.schema, table_name=table.table.name,
                schema_fingerprint=snapshot.fingerprint, columns=groups,
                aggregates=(AggregateSpec(function=function, column=column, alias=f"{function.value}_{column}"),),
                group_by=groups, limit=100,
            )
        if projection:
            names, table_name = projection.groups()
            table = self._table(snapshot, table_name)
            columns = tuple(self._column(table, item.strip()) for item in names.split(","))
            if not columns or len(columns) != len(set(columns)):
                raise SemanticPlanningError("projection columns must be non-empty and unique")
            return QueryPlan(
                schema_name=table.table.schema, table_name=table.table.name,
                schema_fingerprint=snapshot.fingerprint, columns=columns, limit=100,
            )
        raise SemanticPlanningError(
            "unsupported or ambiguous question; use a supported count, aggregate, or projection form"
        )

    def _table(self, snapshot: SchemaSnapshot, requested: str) -> TableSchema:
        physical_name = (
            self.semantic_layer.resolve_table(requested)
            if self.semantic_layer is not None else None
        )
        resolved_name = physical_name or requested
        matches = [
            item for item in snapshot.tables
            if item.table.name.casefold() == resolved_name.casefold()
        ]
        if len(matches) != 1:
            raise SemanticPlanningError(
                f"table is unknown or ambiguous in discovered schema: {requested}"
            )
        return matches[0]

    def _column(self, table: TableSchema, requested: str) -> str:
        semantic_field = (
            self.semantic_layer.resolve_field(table.table.name, requested)
            if self.semantic_layer is not None else None
        )
        resolved_name = semantic_field.physical_column if semantic_field is not None else requested
        matches = [
            item.name for item in table.columns
            if item.name.casefold() == resolved_name.casefold()
        ]
        if len(matches) != 1:
            raise SemanticPlanningError(f"column is unknown in discovered table: {requested}")
        return matches[0]


class EvaluationCase(BaseModel):
    """One fixed semantic-planner regression case."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    question: str = Field(min_length=1)
    expected: QueryPlan


class EvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    total: int = Field(ge=0)
    passed: int = Field(ge=0)
    rejected: int = Field(ge=0)

    @property
    def accuracy(self) -> float:
        return self.passed / self.total if self.total else 0.0


def evaluate_semantic_planner(
    planner: QueryPlanner, *, snapshot: SchemaSnapshot, cases: tuple[EvaluationCase, ...],
) -> EvaluationReport:
    """Compare exact typed plans; invalid/unsupported outputs count as failures."""
    passed = 0
    rejected = 0
    for case in cases:
        try:
            actual = planner.plan(case.question, snapshot)
        except (ValueError, TypeError):
            rejected += 1
            continue
        if actual == case.expected:
            passed += 1
    return EvaluationReport(total=len(cases), passed=passed, rejected=rejected)


@dataclass(frozen=True, slots=True)
class QueryDecisionResult:
    query: QueryPlanningResult
    decision: RuntimeResult | None


class QueryDecisionRuntime:
    """Run bounded query first; optionally pass persisted Evidence through policy."""
    def __init__(
        self, *, query_runtime: QueryPlanningRuntime, evidence_store: SQLiteEvidenceStore,
        trace_store: SQLiteDecisionTraceStore, policy: DecisionPolicy,
    ) -> None:
        self._query_runtime = query_runtime
        self._decision_runtime = DecisionRuntime(
            evidence_store=evidence_store, trace_store=trace_store, policy=policy,
        )

    async def run(
        self, *, question: str, query_planner: QueryPlanner,
        decision_planner: DecisionPlanner | None = None,
        expected_schema_fingerprints: dict[str, str] | None = None,
    ) -> QueryDecisionResult:
        query = await self._query_runtime.run(question, query_planner)
        if decision_planner is None:
            return QueryDecisionResult(query=query, decision=None)
        decision = self._decision_runtime.run(
            question=question, evidence_ids=(query.evidence_id,), planner=decision_planner,
            expected_schema_fingerprints=expected_schema_fingerprints,
        )
        return QueryDecisionResult(query=query, decision=decision)
