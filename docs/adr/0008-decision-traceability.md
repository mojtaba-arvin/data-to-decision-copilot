# ADR 0008: immutable evidence-to-decision trace

## Status

Accepted for Phase 7.

## Decision

Represent the decision lineage as immutable Pydantic contracts and provide deterministic factory and verifier functions over the Phase 6 evidence-store protocol. Each user-facing claim must cite evidence IDs; the claim references, decision references, and trace bindings must be identical sets. The trace captures query/result digests and source/schema/data-version provenance, plus planner and policy version identifiers.

## Rationale

This creates a narrow auditable seam between execution evidence and future planner/API layers without introducing an LLM dependency, persistence backend, or runtime orchestration framework.

## Consequences

- Missing, unexecuted, corrupt, or unbound evidence cannot be used to construct a trace.
- Verification can detect trace mutation, evidence-journal corruption, evidence substitution, and stale schemas when current fingerprints are supplied.
- The digest is not a signature; a trusted independent anchor is still required against full-store rewrites.
- Trace persistence, signatures, planner artifact registry, claim extraction, and user-facing explanation UI remain future work.
