# Spec 0003: bounded data source contracts

## Goal

Introduce a storage-engine-neutral metadata contract and enforce deterministic resource bounds during DuckDB schema discovery.

## Scope

- Define immutable shared metadata records and a structural `DataSource` protocol.
- Define validated positive limits for maximum relations and columns per relation.
- Apply the limits to DuckDB catalog queries at the database boundary using bound parameters.
- Fail closed when a limit is exceeded; never return a silently truncated schema.
- Preserve the existing read-only persistent DuckDB connection behavior and metadata-only public API.

## Security and correctness

- Limits are enforced in SQL with a one-row-over-limit sentinel, not by fetching an unbounded catalog and slicing in Python.
- Limit parameters are bound values.
- The relation list and each relation's columns are independently bounded.
- A schema snapshot fails if any component exceeds a limit; it cannot be mistaken for a complete snapshot.
- This is metadata resource control, not a query execution sandbox or a guarantee against all DuckDB resource exhaustion.

## Acceptance criteria

- [x] A structural protocol describes metadata capabilities without exposing SQL execution.
- [x] Invalid zero/negative limits are rejected at construction.
- [x] Relation discovery fails closed above the configured relation limit.
- [x] Column discovery fails closed above the configured per-relation column limit.
- [x] Snapshot generation inherits both limits.
- [x] Tests verify protocol compatibility and limit behavior.

## Deferred

- CSV/Parquet file adapters and file path policy.
- Analytical SQL execution, statement timeout, cancellation, row/byte caps.
- Execution-derived evidence objects.
- LLM planning and provider integrations.
