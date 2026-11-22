# ADR 0013: keep model output inside a strict typed-plan boundary

## Status

Accepted for Phase 12.

## Decision

Introduce an injectable structured-output provider protocol and a ModelBackedQueryPlanner adapter. Accept only bounded strict JSON that validates as the existing QueryPlan and carries the current schema fingerprint. Continue to delegate identifier validation, SQL compilation, execution, and evidence creation to the established runtime.

## Rationale

A provider integration should not expand the model's authority. Keeping the model at a typed data boundary allows multiple local or hosted providers to be evaluated without changing SQL safety or decision policy.

## Consequences

- No provider SDK or inference spend is required to use or test the core.
- Provider responses are untrusted and fail closed.
- The declared model release identity is configuration metadata, not cryptographic attestation.
- A concrete inference adapter remains a separately configurable integration.
