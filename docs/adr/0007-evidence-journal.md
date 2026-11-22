# ADR 0007: SQLite append-only evidence journal

## Status
Accepted for Phase 6.

## Decision
Use Python's standard-library SQLite as the first durable local journal. Each append is serialized by an immediate transaction, and each row commits a canonical record digest plus a previous-entry-linked chain digest. Database triggers reject ordinary UPDATE and DELETE statements. A separate verifier recomputes evidence-level, record-level, and chain-level digests.

## Rationale
This provides durable local persistence and deterministic verification without adding a runtime dependency or claiming distributed-database guarantees.

## Consequences
- A single database file is the durability boundary; backups remain operational responsibility.
- Schema version mismatch fails closed; migrations are explicit future work.
- Triggers are guardrails, not a security boundary against an attacker who controls the database file/schema.
- Hash chains are not signatures. Full-database rewrite is detectable only when compared with an independently retained chain head.
- A future server deployment may add another adapter after its transactional and consistency semantics are specified.
