# Changelog

All notable changes are recorded here. This project has not published a stable
release yet; entries remain under Unreleased until a tagged release is created.

## Unreleased

### Added
- An isolated, disabled-by-default provider-neutral DecisionPlan generation contract with journal-verified bounded evidence, strict JSON schemas, semantic verification hooks, deterministic abstention, and provenance; production activation remains blocked on Issue #31.
- Typed evidence, decision, and trace contracts with deterministic policy checks.
- Read-only DuckDB discovery and bounded analytical execution.
- Explicit CSV/Parquet sources with bounded no-follow input snapshots and schema-drift checks.
- Execution-derived evidence, append-only SQLite journals, hash-chain verification, and historical trace replay.
- Schema-grounded QueryPlans, deterministic Persian/English semantic planning, and an optional loopback-only Ollama structured-plan provider.
- Shared local REST, CLI, MCP stdio, and browser interfaces.
- Bounded SQLite question history with bind-parameter redaction.
- SQL and prompt-injection regression suites, dependency audit, benchmark harness, offline installation guide, and synthetic end-to-end example.

### Security and operational limitations
- Remote REST binding supports only trusted single-tenant deployments protected by TLS and a shared bearer token; per-user identity, RBAC, quotas, and tenant isolation are not implemented.
- DuckDB resource limits are not OS-level sandboxing.
- Real Ollama inference is an opt-in check, separate from mocked CI.
- Evidence and DecisionTrace journals are append-only and do not yet provide automatic retention or compaction.
- Direct contradiction checks are narrow heuristics, not general natural-language entailment.
