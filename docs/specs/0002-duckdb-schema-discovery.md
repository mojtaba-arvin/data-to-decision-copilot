# Spec 0002: DuckDB schema discovery

## Goal

Add a read-only DuckDB catalog adapter that discovers tables, views, columns, nullability, types, and a deterministic schema fingerprint.

## Scope

- Open persistent DuckDB database files using DuckDB's read-only connection mode.
- Support an isolated in-memory connection for tests and ephemeral workflows.
- Enumerate base tables by default; views are opt-in.
- Describe one relation using parameterized metadata queries.
- Produce a stable SHA-256 fingerprint over normalized schema metadata.
- Expose metadata only; no arbitrary SQL execution API in this milestone.

## Acceptance criteria

- [ ] Persistent sources are opened with read_only enabled.
- [ ] Table discovery excludes system schemas and excludes views by default.
- [ ] Column metadata is ordered and preserves DuckDB type and nullability.
- [ ] Missing relations fail closed with a connector-specific exception.
- [ ] Metadata lookup values are bound parameters, not interpolated SQL.
- [ ] Fingerprints are stable for identical schemas and change when schema changes.
- [ ] Tests run offline against temporary DuckDB files.

## Non-goals

- CSV, Parquet, Excel registration/import.
- SQL query execution, timeout enforcement, row/byte caps.
- PII classification, semantic models, LLM planning, or caching.

## Security

The connector exposes no public query or mutation method. Persistent files use DuckDB's read-only mode. Schema names and table names are treated as data and bound as query parameters. A later query executor must independently enforce SQL AST policy, read-only database permissions, timeouts, and output limits.
