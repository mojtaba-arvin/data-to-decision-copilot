# ADR 0004: explicit bounded file sources

## Status
Accepted for the Phase 3 implementation.

## Decision
Implement a dedicated FileDataSource for explicitly listed CSV and Parquet files. Require relative paths beneath a caller-selected root, reject symlinks and traversal, enforce a pre-open file-size ceiling, bound CSV schema inference, and reuse the shared relation/column limits.

Use an isolated in-memory DuckDB connection and expose only the DataSource metadata protocol. Assign generated relation names rather than deriving SQL identifiers from filenames.

## Consequences
- No arbitrary path, glob, or SQL surface is exposed.
- File paths remain caller-controlled configuration and must be protected from concurrent untrusted mutation.
- CSV inference may not discover rare values/types outside its sample; downstream planning must treat inferred types as provisional.
- The adapter does not provide row execution or evidence generation.
