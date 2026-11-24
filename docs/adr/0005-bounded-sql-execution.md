# ADR 0005: isolated bounded DuckDB query execution

## Status
Accepted for the Phase 4 implementation.

## Decision
Use a dedicated executor rather than adding SQL execution to metadata connectors. Validate SQL through SQLGlot with a default-deny function allowlist. Open a fresh DuckDB connection for each request, use read-only mode for persistent databases, disable external access and extension installation/loading, and apply per-query thread, memory, and spill settings.

Run synchronous DuckDB work in a worker thread. On deadline or task cancellation, interrupt only that request's connection, await worker termination, and close the connection before allowing another query. Bound fetched output by rows and serialized bytes.

## Consequences
- The executor does not share mutable connection state with schema-discovery adapters.
- Every request pays connection setup cost in exchange for isolation and reliable cleanup.
- Timeout is enforced by DuckDB interruption, not by abandoning a still-running thread.
- The executor reports captured rows and truncation only; it does not claim a total result count.
- OS-level sandboxing and immutable evidence generation remain future work.
