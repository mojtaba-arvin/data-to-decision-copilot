# ADR 0010: deterministic policy gate before decision trace persistence

## Status

Accepted for Phase 9.

## Decision

Introduce a narrow `Planner` protocol, immutable planner identity, structured `DecisionPlan`, content-addressed `DecisionPolicy`, and a `DecisionRuntime` orchestration boundary. The runtime resolves evidence from the existing journal, validates optional schema freshness, invokes the planner, evaluates policy, and only then creates and persists a decision trace.

The first concrete planner is a deterministic adapter for an explicit proposal. It is deliberately not represented as an autonomous natural-language-to-SQL planner.

## Rationale

Separating proposal generation from policy enforcement prevents provider-specific logic from becoming the security boundary. A deterministic policy gate can be tested without model calls and can reject unsupported numeric statements before persistence. Content-addressed planner and policy references improve trace reproducibility compared with free-form caller-supplied version strings.

## Consequences

- Rejected decisions do not enter the trace journal.
- Every accepted trace carries planner and policy references including SHA-256 identifiers.
- The reference planner validates request/evidence scope but does not infer business semantics.
- Numeric grounding is a conservative lexical check, not proof of semantic correctness.
- Artifact identity depends on the planner supplying a truthful artifact digest; a deployment attestation system remains future work.
