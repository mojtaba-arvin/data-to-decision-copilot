# ADR 0011: compile typed query plans, never model-authored SQL

## Status

Accepted for Phase 10.

## Decision

Introduce a provider-independent QueryPlanner protocol that returns a strict Pydantic QueryPlan. Resolve every relation and column against the current bounded SchemaSnapshot, bind all filter values, quote only validated identifiers, compile a narrow analytical SQL subset, and run the generated statement through both the existing SQL AST guard and bounded read-only executor.

Persist execution-derived evidence only after the executor returns its bounded QueryResult. Record planner and prompt digests with the query-planning result.

## Rationale

Passing model-generated SQL directly to an executor creates an unnecessarily broad attack surface. A typed plan narrows the model's authority to choosing among discovered identifiers and supported analytical operations. SQL generation, parameter binding, AST validation, and resource limits remain deterministic application responsibilities.

## Consequences

- The planner cannot issue arbitrary SQL, writes, file reads, extension operations, or unbounded queries.
- Unknown schema references and stale plans fail closed.
- Query result evidence is derived only from executor output.
- The deterministic adapter is a contract fixture, not an autonomous NL semantic model.
- FileDataSource execution and transactional schema/data snapshots remain future work.
