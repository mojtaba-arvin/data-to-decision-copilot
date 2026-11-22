# Spec 0001: Phase 0 foundation

## Goal

Establish the smallest testable foundation for an evidence-first, read-only analytics copilot before adding model orchestration or external data access.

## Scope

1. Define provider-independent Pydantic contracts for evidence, alternatives, decisions, and copilot responses.
2. Reject decisions that cite evidence not attached to the response.
3. Reject recommendations when the response explicitly declares insufficient data.
4. Implement an AST-based SQL gate that accepts one read-only query and rejects non-query statements, multiple statements, and known dangerous functions.
5. Add offline deterministic tests and strict project tooling configuration.

## Non-goals

- Connecting to a database or executing SQL.
- Calling an LLM or selecting a default provider.
- Claiming that AST validation alone makes arbitrary SQL safe.
- REST, MCP, CLI, UI, semantic-layer adapters, caching, telemetry, or benchmark claims.

## Acceptance criteria

- [ ] Pydantic rejects invalid evidence references and inconsistent evidence metadata.
- [ ] Pydantic rejects recommendations on insufficient-data responses.
- [ ] SQL parser accepts SELECT, CTE, and set-operation queries.
- [ ] SQL parser rejects writes, DDL, multiple statements, external file/network functions, and malformed SQL.
- [ ] Unit tests require no network, database, or model credentials.
- [ ] Ruff and Pyright strict checks pass.
- [ ] Security documentation states that database-enforced read-only permissions and execution limits remain mandatory.

## Security notes

SQL is parsed into an AST; regex is not used as the security boundary. The database must still enforce read-only permissions. This initial policy is intentionally conservative and must be extended with dialect-specific adversarial tests before enabling execution.
