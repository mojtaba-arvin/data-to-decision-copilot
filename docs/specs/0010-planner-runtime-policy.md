# Spec 0010: provider-independent planner runtime and decision policy

## Goal

Introduce an explicit planner boundary and deterministic policy enforcement between verified execution evidence and persisted decision traces. A decision is not published or persisted unless every policy check passes.

## Contracts

- `Planner` accepts a question and immutable, journal-verified `Evidence` records and returns a typed `DecisionPlan`.
- `PlannerIdentity` includes planner ID, version, and a SHA-256 artifact identifier. The identifier is recorded in Phase 7's `planner_version` field.
- `DecisionPolicy` is immutable and content-addressed. Its reference includes policy ID, version, and SHA-256 of the canonical policy definition; this reference is recorded in `policy_version`.
- `DecisionRuntime` verifies the evidence journal, resolves requested evidence, optionally checks current schema fingerprints, invokes the planner, enforces policy, then creates and persists a Phase 7 trace.
- Policy checks include evidence existence and linkage, minimum evidence count, allowed confidence, optional reversal conditions, truncated-evidence rejection, and conservative numeric literal grounding against numeric values in the referenced result rows.
- A policy rejection raises `PolicyViolation`; no trace is appended.

## Numeric grounding

Numeric tokens in the decision action, rationale, and claim statements must match numeric scalar values in the referenced evidence rows. The check is intentionally conservative and lexical. It does not establish units, aggregation semantics, statistical validity, or whether a sentence logically follows from a row. It may reject legitimate derived numbers; such calculations must be explicitly executed and captured as evidence before being stated.

## Deterministic reference planner

`DeterministicPlanner` is a provider-independent reference adapter for a pre-authored structured proposal. It validates that the proposal only references evidence supplied to the planner. It is not an autonomous semantic planner and does not generate SQL from natural language. Future model-backed planners must implement the same protocol and provide an immutable artifact digest from the build/release pipeline.

## Trust boundaries

- Planner output is untrusted until policy evaluation succeeds.
- Evidence is loaded from the append-only journal, not authored by the planner.
- Policy evaluation occurs before trace persistence.
- Planner artifact digests are supplied by the planner implementation; runtime records but cannot independently prove that the deployed binary matches that digest.
- Policy hashes identify the exact policy definition fields. They do not prove organizational approval or correctness.
- Numeric grounding is not semantic fact verification.
- This phase does not execute SQL, integrate an LLM provider, perform writes, or take external actions.
