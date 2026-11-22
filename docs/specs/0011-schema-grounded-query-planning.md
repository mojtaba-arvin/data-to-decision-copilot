# Spec 0011: schema-grounded query planning and execution

## Goal

Add a typed planning boundary between a natural-language question and the existing bounded, read-only DuckDB executor. The planner may propose a structured QueryPlan; it never supplies executable SQL.

## QueryPlan contract

A plan identifies one discovered schema/table, the exact schema fingerprint used during planning, selected columns, optional aggregates, filters, grouping, output ordering, and a row limit. Identifiers are resolved against a fresh SchemaSnapshot. Unknown relations/columns, stale fingerprints, ambiguous output names, unsupported identifier spellings, invalid aggregate/grouping combinations, and unselected sort keys fail closed.

Supported operations are deliberately small: equality/inequality and ordered scalar comparisons, bounded IN/NOT IN, null checks, COUNT/SUM/AVG/MIN/MAX, grouping, ordering, and a hard maximum of 1,000 rows. Filter values are always bound parameters. Identifiers are quoted only after schema resolution and must match a conservative identifier grammar.

## Execution and evidence

QueryPlanningRuntime:
1. Reads a bounded DataSource schema snapshot.
2. Requests a typed QueryPlan from a provider-independent QueryPlanner.
3. Checks schema fingerprint and all referenced identifiers.
4. Compiles the plan and runs the result through SQLGlot's existing read-only AST validator.
5. Executes only through DuckDBQueryExecutor, which independently validates SQL and applies read-only, timeout, memory, row, and byte limits.
6. Creates execution-derived Evidence from the actual QueryResult and appends it to SQLiteEvidenceStore.

No model-authored SQL is accepted. A plan cannot select arbitrary files, attach databases, issue writes, choose SQL functions, or raise the configured row cap. Planner identity and prompt digest are returned with the execution result for downstream trace recording.

## Planner adapters

QueryPlanner is the adapter boundary for model-backed semantic planners. DeterministicQueryPlanner is an offline contract fixture for a pre-authored QueryPlan; it does not claim to interpret arbitrary natural language. The deterministic adapter hashes its version and plan template. A production model adapter must provide truthful immutable artifact and prompt-template digests from its release configuration.

## Limitations

- Natural-language interpretation is not implemented by the deterministic adapter; no paid or network model is invoked.
- Query execution currently uses DuckDBQueryExecutor's database path. FileDataSource discovery can describe CSV/Parquet metadata, but this phase does not execute against its isolated in-memory connection.
- The source adapter and executor must refer to the same underlying DuckDB database; callers are responsible for that configuration.
- The schema snapshot is checked before execution. Concurrent schema/data mutation after discovery is not transactionally pinned by this adapter.
- Identifier grammar is intentionally conservative; unusual quoted identifiers are rejected.
- Prompt digest and planner artifact identity are adapter-supplied metadata, not independent deployment attestation.
- Query results are capped and may be truncated. Downstream policy must reject or explicitly handle truncated evidence.
- The current planner result is not automatically converted into a DecisionPlan. Phase 9's policy-gated decision runtime remains a separate downstream step.
