# ADR 0001: License and initial runtime

- **Status:** Accepted for initial foundation
- **Date:** 2026-11-22

## Context

The repository was initialized with an MIT license. The engineering brief proposes Apache-2.0, but changing an already committed repository license is a governance decision with downstream consequences.

## Decision

Retain the existing MIT license for the initial implementation. Revisit only through an explicit project-level decision with a license impact review.

Use Python 3.13+, Pydantic v2, SQLGlot, pytest, Ruff, and Pyright strict as the initial backend foundation. Keep the runtime provider-independent and avoid an agent framework until concrete state-machine complexity justifies one.

## Consequences

- No license mismatch is introduced between repository metadata and package metadata.
- The first milestone has a small dependency surface.
- Provider, connector, API, and MCP choices remain open until their interfaces are specified.
