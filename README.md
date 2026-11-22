# Data-to-Decision Copilot

An open-source, read-only analytics copilot that turns natural-language questions into evidence-backed answers and explicit decision recommendations.

> Status: early development. The current milestone establishes the Python core contracts and SQL safety boundary; it is not yet a production-ready analytics agent.

## Design principles
- **Evidence before explanation:** every reported metric must trace to an executed query or a recorded deterministic calculation.
- **Read-only by construction:** generated SQL is parsed and constrained to a single read-only query; database credentials must independently enforce read-only access.
- **Honest uncertainty:** insufficient, ambiguous, or unavailable evidence must produce an explicit abstention rather than an invented answer.
- **Provider and database independence:** integrations sit behind narrow interfaces.
- **Multilingual from day one:** language is preserved through the pipeline; presentation layers must support RTL and ICU localization.

## Repository status
- Typed Pydantic v2 decision and evidence contracts.
- AST-based SQL validation using SQLGlot.
- Read-only DuckDB catalog discovery and bounded CSV/Parquet metadata sources.
- A bounded DuckDB executor with read-only file access, conservative function allowlisting, query interruption, and row/byte ceilings.
- Phase 5 immutable execution-derived evidence with query/result digests and source/schema provenance.
- Phase 6 durable local SQLite journal with transactional sequencing, append-only triggers, and independent chain verification.
- Phase 7 immutable decision traces linking each claim to exact execution evidence, query/result digests, source/schema provenance, and planner/policy versions.
- Deterministic tests that do not call an LLM or access a network.

The journal is a local persistence adapter, not a distributed production ledger.

## Development
Requires Python 3.13+ and uv.
```bash
uv sync --all-groups
uv run pytest
uv run ruff check .
uv run pyright
```

## Security boundary
SQL validation is defense in depth, not a replacement for database-enforced read-only credentials, OS-level isolation, or network policy. The DuckDB memory ceiling is not an OS process sandbox. Never connect this project to production using a privileged account.

Journal limitations and operating guidance are documented in [Spec 0007](docs/specs/0007-evidence-journal.md) and [ADR 0007](docs/adr/0007-evidence-journal.md). Decision lineage and verification are specified in [Spec 0008](docs/specs/0008-decision-traceability.md) and [ADR 0008](docs/adr/0008-decision-traceability.md).

## License
MIT. See [LICENSE](LICENSE).
