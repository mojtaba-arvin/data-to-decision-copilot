"""Schema-grounded analytical query planning and bounded execution."""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from d2d.connectors.base import DataSource, SchemaSnapshot, TableSchema
from d2d.core.evidence import create_execution_evidence
from d2d.core.evidence_store import SQLiteEvidenceStore
from d2d.core.sql_executor import DuckDBQueryExecutor, QueryResult
from d2d.core.sql_guard import validate_read_only_sql

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_LIMIT = 1_000
_MAX_FILTER_VALUES = 100


class QueryPlanningError(ValueError):
    """Raised when a proposed plan cannot be grounded in the current schema."""


class FilterOperator(StrEnum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    NOT_IN = "not_in"
    IS_NULL = "is_null"
    IS_NOT_NULL = "is_not_null"


class SortDirection(StrEnum):
    ASC = "asc"
    DESC = "desc"


class AggregateFunction(StrEnum):
    COUNT = "count"
    SUM = "sum"
    AVG = "avg"
    MIN = "min"
    MAX = "max"


class QueryFilter(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    column: str = Field(min_length=1)
    operator: FilterOperator
    value: object | None = None

    @model_validator(mode="after")
    def validate_value(self) -> QueryFilter:
        if self.operator in {FilterOperator.IS_NULL, FilterOperator.IS_NOT_NULL}:
            if self.value is not None:
                raise ValueError("null operators must not include a value")
        elif self.operator in {FilterOperator.IN, FilterOperator.NOT_IN}:
            if not isinstance(self.value, (tuple, list)) or not self.value:
                raise ValueError("IN operators require a non-empty list of values")
            if len(self.value) > _MAX_FILTER_VALUES:
                raise ValueError(f"IN filters are limited to {_MAX_FILTER_VALUES} values")
            if any(not _is_scalar(item) for item in self.value):
                raise ValueError("filter values must be scalar")
        elif not _is_scalar(self.value):
            raise ValueError("filter values must be scalar; use IS NULL for null checks")
        return self


class AggregateSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    function: AggregateFunction
    column: str | None = None
    alias: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_target(self) -> AggregateSpec:
        if self.function is AggregateFunction.COUNT and self.column is None:
            return self
        if self.column is None:
            raise ValueError(f"{self.function.value} requires a column")
        return self


class QuerySort(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    output_name: str = Field(min_length=1)
    direction: SortDirection = SortDirection.ASC


class QueryPlan(BaseModel):
    """Provider output is data, never executable SQL."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_name: str = Field(min_length=1)
    table_name: str = Field(min_length=1)
    schema_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    columns: tuple[str, ...] = ()
    aggregates: tuple[AggregateSpec, ...] = ()
    filters: tuple[QueryFilter, ...] = ()
    group_by: tuple[str, ...] = ()
    order_by: tuple[QuerySort, ...] = ()
    limit: int = Field(default=100, ge=1, le=_MAX_LIMIT)

    @model_validator(mode="after")
    def validate_shape(self) -> QueryPlan:
        if not self.columns and not self.aggregates:
            raise ValueError("query plan must select at least one column or aggregate")
        if len(self.columns) != len(set(self.columns)):
            raise ValueError("selected columns must be unique")
        if len(self.group_by) != len(set(self.group_by)):
            raise ValueError("group_by columns must be unique")
        if len({item.alias for item in self.aggregates}) != len(self.aggregates):
            raise ValueError("aggregate aliases must be unique")
        if set(self.group_by) - set(self.columns):
            raise ValueError("group_by columns must also be selected")
        if self.aggregates and set(self.columns) != set(self.group_by):
            raise ValueError("every non-aggregate selected column must be included in GROUP BY")
        return self


def _is_scalar(value: object) -> bool:
    return isinstance(value, (str, int, float, bool))


def _quote_identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise QueryPlanningError(f"unsupported identifier: {value!r}")
    return '"' + value.replace('"', '""') + '"'


def _table_for_plan(plan: QueryPlan, snapshot: SchemaSnapshot) -> TableSchema:
    if plan.schema_fingerprint != snapshot.fingerprint:
        raise QueryPlanningError("query plan schema fingerprint is stale")
    matches = [
        item for item in snapshot.tables
        if item.table.schema == plan.schema_name and item.table.name == plan.table_name
    ]
    if len(matches) != 1:
        raise QueryPlanningError("query plan table is not present in the current schema")
    return matches[0]


@dataclass(frozen=True, slots=True)
class CompiledQuery:
    sql: str
    parameters: tuple[object, ...]
    dialect: str
    source_id: str
    schema_fingerprint: str


def compile_query_plan(
    plan: QueryPlan, *, snapshot: SchemaSnapshot, source_id: str
) -> CompiledQuery:
    """Compile only schema-resolved identifiers and bound filter parameters."""
    if not source_id.strip():
        raise QueryPlanningError("source_id must not be empty")
    table = _table_for_plan(plan, snapshot)
    known = {item.name for item in table.columns}
    referenced = (
        set(plan.columns) | set(plan.group_by)
        | {item.column for item in plan.aggregates if item.column is not None}
        | {item.column for item in plan.filters}
    )
    unknown = referenced - known
    if unknown:
        raise QueryPlanningError(f"unknown columns in query plan: {sorted(unknown)}")

    output_names = list(plan.columns) + [item.alias for item in plan.aggregates]
    if len(output_names) != len(set(output_names)):
        raise QueryPlanningError("selected columns and aggregate aliases must be unique")
    if set(item.output_name for item in plan.order_by) - set(output_names):
        raise QueryPlanningError("sort keys must reference selected output names")

    projections = [_quote_identifier(item) for item in plan.columns]
    for aggregate in plan.aggregates:
        target = "*" if aggregate.column is None else _quote_identifier(aggregate.column)
        projections.append(
            f"{aggregate.function.value.upper()}({target}) AS {_quote_identifier(aggregate.alias)}"
        )

    table_ref = (
        f"{_quote_identifier(table.table.catalog)}."
        f"{_quote_identifier(plan.schema_name)}.{_quote_identifier(plan.table_name)}"
    )
    sql = f"SELECT {', '.join(projections)} FROM {table_ref}"
    parameters: list[object] = []
    if plan.filters:
        clauses: list[str] = []
        operators = {
            FilterOperator.EQ: "=",
            FilterOperator.NE: "<>",
            FilterOperator.GT: ">",
            FilterOperator.GTE: ">=",
            FilterOperator.LT: "<",
            FilterOperator.LTE: "<=",
        }
        for item in plan.filters:
            column = _quote_identifier(item.column)
            if item.operator in operators:
                clauses.append(f"{column} {operators[item.operator]} ?")
                parameters.append(item.value)
            elif item.operator in {FilterOperator.IN, FilterOperator.NOT_IN}:
                values = tuple(item.value)  # validated as a non-empty tuple/list
                placeholders = ", ".join("?" for _ in values)
                keyword = "NOT IN" if item.operator is FilterOperator.NOT_IN else "IN"
                clauses.append(f"{column} {keyword} ({placeholders})")
                parameters.extend(values)
            elif item.operator is FilterOperator.IS_NULL:
                clauses.append(f"{column} IS NULL")
            else:
                clauses.append(f"{column} IS NOT NULL")
        sql += " WHERE " + " AND ".join(f"({item})" for item in clauses)

    if plan.group_by:
        sql += " GROUP BY " + ", ".join(_quote_identifier(item) for item in plan.group_by)
    if plan.order_by:
        sql += " ORDER BY " + ", ".join(
            f"{_quote_identifier(item.output_name)} {item.direction.value.upper()}"
            for item in plan.order_by
        )
    sql += " LIMIT ?"
    parameters.append(plan.limit)

    validated = validate_read_only_sql(sql, dialect="duckdb")
    return CompiledQuery(
        sql=validated.sql,
        parameters=tuple(parameters),
        dialect=validated.dialect,
        source_id=source_id,
        schema_fingerprint=snapshot.fingerprint,
    )


class QueryPlannerIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    planner_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @property
    def reference(self) -> str:
        return f"{self.planner_id}@{self.version}#sha256:{self.artifact_sha256}"


class QueryPlanner(Protocol):
    """Model/provider adapter: natural-language intent to typed QueryPlan."""

    @property
    def identity(self) -> QueryPlannerIdentity: ...

    def plan(self, question: str, snapshot: SchemaSnapshot) -> QueryPlan: ...


@dataclass(frozen=True, slots=True)
class DeterministicQueryPlanner:
    """Offline reference adapter for a pre-authored plan, not an NL model."""

    template: QueryPlan
    planner_id: str = "deterministic-query-plan"
    planner_version: str = "1"

    @property
    def identity(self) -> QueryPlannerIdentity:
        canonical = json.dumps(
            {
                "planner_id": self.planner_id,
                "version": self.planner_version,
                "template": self.template.model_dump(mode="json"),
            },
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        prompt = hashlib.sha256(b"pre-authored-query-plan:v1").hexdigest()
        return QueryPlannerIdentity(
            planner_id=self.planner_id,
            version=self.planner_version,
            artifact_sha256=hashlib.sha256(canonical).hexdigest(),
            prompt_sha256=prompt,
        )

    def plan(self, question: str, snapshot: SchemaSnapshot) -> QueryPlan:
        if not question.strip():
            raise QueryPlanningError("question must not be empty")
        if self.template.schema_fingerprint != snapshot.fingerprint:
            raise QueryPlanningError("query plan schema fingerprint is stale")
        return self.template


@dataclass(frozen=True, slots=True)
class QueryPlanningResult:
    query: CompiledQuery
    result: QueryResult
    evidence_id: str
    evidence_sequence: int
    planner_reference: str
    prompt_sha256: str


class QueryPlanningRuntime:
    """Discover schema, plan, compile, execute, and persist execution evidence."""

    def __init__(
        self,
        *,
        source_id: str,
        data_source: DataSource,
        executor: DuckDBQueryExecutor,
        evidence_store: SQLiteEvidenceStore,
    ) -> None:
        if not source_id.strip():
            raise ValueError("source_id must not be empty")
        self._source_id = source_id
        self._data_source = data_source
        self._executor = executor
        self._evidence_store = evidence_store

    async def run(self, question: str, planner: QueryPlanner) -> QueryPlanningResult:
        if not question.strip():
            raise QueryPlanningError("question must not be empty")
        snapshot = self._data_source.snapshot()
        identity = planner.identity
        plan = planner.plan(question, snapshot)
        query = compile_query_plan(plan, snapshot=snapshot, source_id=self._source_id)
        started = time.perf_counter()
        result = await self._executor.execute(
            query.sql, query.parameters, dialect=query.dialect
        )
        execution_ms = (time.perf_counter() - started) * 1_000
        evidence = create_execution_evidence(
            sql=query.sql,
            dialect=query.dialect,
            result=result,
            source_id=query.source_id,
            schema_fingerprint=query.schema_fingerprint,
            execution_ms=execution_ms,
        )
        sequence = self._evidence_store.append(evidence)
        return QueryPlanningResult(
            query=query,
            result=result,
            evidence_id=evidence.evidence_id,
            evidence_sequence=sequence,
            planner_reference=identity.reference,
            prompt_sha256=identity.prompt_sha256,
        )
