# ADR 0012: constrain offline semantic parsing and keep decisions explicit

## Status

Accepted for Phase 11.

## Decision

Add a deterministic offline grammar adapter that resolves explicit table/column names against SchemaSnapshot and emits only the existing typed QueryPlan. Add an exact-match regression evaluator. Add an orchestration bridge that runs the bounded query first and optionally hands its persisted Evidence to Phase 9 DecisionRuntime.

## Rationale

A small, testable grammar advances end-to-end usability without falsely claiming LLM capability or introducing paid/network dependencies. Reusing Phase 9 preserves evidence verification, numeric grounding, policy gating, and trace persistence.

## Consequences

- Unsupported or ambiguous natural language fails closed.
- No automatic decision is generated without an explicit decision planner.
- SQL construction and execution remain exclusively in the existing guarded runtime.
- A true model-backed adapter remains a future, separately reviewed integration.
