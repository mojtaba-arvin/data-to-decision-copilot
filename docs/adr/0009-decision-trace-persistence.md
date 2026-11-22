# ADR 0009: append-only SQLite persistence for decision traces

## Status

Accepted for Phase 8.

## Decision

Add a separate `SQLiteDecisionTraceStore` rather than modifying the Phase 6 evidence journal. Store canonical Phase 7 trace payloads in a transactional, sequential, hash-chained table with unique trace IDs and UPDATE/DELETE guards. Expose append, get, count, verify, and replay operations.

## Rationale

Decision traces and execution evidence have different lifecycles and contracts. A separate adapter keeps the established evidence journal stable, permits independent schema versioning, and makes the additional trust boundary explicit. Reusing Phase 7 verification avoids duplicating evidence-provenance rules.

## Consequences

- Trace persistence is durable across process restarts and ordered under concurrent appends.
- Verification without an evidence store checks the trace journal and embedded trace digests; verification with an evidence store additionally validates evidence provenance and optional schema freshness.
- Replay is historical retrieval plus verification, not SQL re-execution or LLM/planner reconstruction.
- SQLite owner access, full-store rewrites, missing external anchors, absent encryption/signatures, and caller-supplied planner/policy version identifiers remain documented limitations.
