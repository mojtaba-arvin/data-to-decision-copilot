# Spec 0009: durable decision trace journal and historical replay

## Scope

Persist immutable Phase 7 decision traces in a local SQLite append-only journal. Support lookup by trace ID, ordered verification, and historical retrieval with optional revalidation against the Phase 6 evidence journal and current schema fingerprints.

## Persistence contract

Each row contains a monotonically increasing sequence, unique trace ID, canonical serialized trace payload, payload SHA-256, previous-entry SHA-256, and entry SHA-256. Appends use `BEGIN IMMEDIATE` so concurrent writers receive a single committed order. Duplicate trace IDs fail atomically. SQLite triggers reject ordinary UPDATE and DELETE operations. A schema-version metadata record makes unsupported journal versions fail closed.

The entry digest covers the schema version, sequence, trace ID, payload digest, and previous entry digest. Verification checks contiguous ordering, chain links, payload parsing, trace ID consistency, the Phase 7 trace digest, payload digest, and entry digest.

## Historical replay

`get(trace_id)` retrieves the immutable serialized snapshot. `replay(...)` retrieves it only after verifying the entire trace journal and, when supplied, the complete evidence journal, evidence provenance, and current schema fingerprints. Replay does not execute SQL, call an LLM, reproduce hidden planner reasoning, or regenerate a recommendation. It is retrieval and verification of the recorded historical decision.

Without an evidence store, `verify()` validates only trace-journal persistence integrity and each embedded trace digest. Supplying an evidence store additionally checks Phase 7 evidence provenance. Current schema freshness is checked only when fingerprints are explicitly supplied.

## Trust boundaries and limitations

- SQLite is a local persistence adapter, not a distributed ledger or multi-host coordination mechanism.
- Triggers deter ordinary application-level mutation but do not protect against a database owner who can drop triggers or rewrite the file.
- SHA-256 chaining detects accidental or partial tampering, but a full-store rewrite can be recomputed unless a trusted external checkpoint exists.
- There are no signatures, encryption, external anchoring, backups, retention policy, or migrations beyond fail-closed schema-version detection.
- Planner and policy version values remain caller-supplied identifiers; no artifact registry or executable planner replay is introduced.
- Trace replay proves what was recorded and whether its captured provenance still matches; it does not prove the semantic correctness of the decision.
