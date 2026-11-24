# ADR 0002: DuckDB read-only catalog adapter

- **Status:** Accepted
- **Date:** 2026-11-22

## Decision

Use DuckDB's Python API for the first local analytical source. Persistent database files are opened with `read_only=True`. The adapter exposes catalog discovery only and does not expose a general-purpose SQL execution method.

An in-memory connection is permitted for isolated tests and ephemeral use; it is process-local and not a persistent user data source.

## Consequences

- No server is required for the first end-to-end local data workflow.
- SQL execution remains a separate, security-reviewed capability.
- CSV/Parquet/Excel ingestion is deferred until file-source lifecycle and extension behavior are specified.
