# ADR 0014: use a loopback-only Ollama adapter first

## Status

Accepted for Phase 13.

## Decision

Implement Ollama's local generate API behind the existing `StructuredPlanProvider` protocol. Use the standard library, JSON Schema output mode, a finite timeout, strict response-size bounds, and loopback-only HTTP.

## Rationale

A local Ollama service enables real inference without paid hosted APIs and fits the project's offline-first direction. Keeping it behind the Phase 12 protocol preserves provider independence and leaves SQL authority in the existing runtime.

## Security and reliability consequences

- The adapter refuses non-loopback hosts so schema metadata cannot be forwarded to arbitrary endpoints.
- Generation is non-streaming and bounded; no retries are performed.
- The adapter cannot execute SQL and does not parse or trust the generated QueryPlan.
- Network behavior is isolated and testable through a mocked standard-library transport.
- Actual inference remains an operator-run integration check requiring a locally installed Ollama model.
