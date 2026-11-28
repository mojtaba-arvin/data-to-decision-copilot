# ADR 0017: keep model-backed decision generation behind semantic release gate

## Status

Accepted as an engineering boundary; runtime activation deferred to Issue #31.

## Decision

Define a provider-independent structured DecisionPlan generation protocol,
strict bounded output schema, bounded evidence projection, deterministic
abstention behavior, and explicit provenance. Keep the implementation isolated
from the default query/decision runtime. Generation is disabled by default and
cannot proceed without an explicitly configured release-eligible semantic
verifier.

## Rationale

The existing deterministic policy checks prove evidence linkage, truncation
status, numeric literal grounding, and a narrow class of direct contradictions.
They do not prove general Persian/English entailment. The checked-in semantic
corpus is explicitly development-only and not human-reviewed. Wiring a model
planner into the persisted decision path before Issue #31 reaches its measured
operating point would turn an acknowledged semantic gap into an unsafe default.

## Consequences

- Provider choice is replaceable and no SQL execution capability crosses the
  provider boundary.
- Unsupported, contradicted, unknown, malformed, out-of-scope, or timed-out
  outputs abstain; no failed plan is persisted.
- Test doubles validate interface behavior only and cannot satisfy Issue #31.
- A future activation requires independently reviewed bilingual holdout results,
  declared release thresholds, trace-provenance persistence, and separate review.
