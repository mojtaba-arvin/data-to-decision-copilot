# ADR 0003: bounded source-neutral metadata discovery

- **Status:** Accepted
- **Date:** 2026-11-22

## Decision

Define a structural `DataSource` protocol around relation listing, relation description, and deterministic schema snapshots. Keep shared immutable metadata records outside any concrete connector.

Every adapter must enforce explicit discovery limits and fail closed rather than returning partial metadata that downstream planning could interpret as complete. The first implementation applies relation and per-relation column limits to DuckDB using parameterized LIMIT values and a one-row-over-limit sentinel.

## Consequences

- Future CSV, Parquet, and server database adapters can implement the same metadata contract.
- Downstream consumers can depend on a stable interface instead of DuckDB-specific types.
- Limits bound metadata cardinality but do not replace file-size controls, query execution limits, OS isolation, or database permissions.
- File adapters remain deferred until path traversal, symlink, glob, file-size, and parser resource policies are implemented and tested.
