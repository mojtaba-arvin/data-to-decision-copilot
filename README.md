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

The first implementation milestone is deliberately small:

- Typed decision and evidence contracts (Pydantic v2).
- AST-based SQL validation using SQLGlot (no regex-based SQL security).
- Read-only DuckDB catalog discovery for persistent database files.
- A structural DataSource protocol shared by metadata adapters.
- Bounded relation and column discovery; limit violations fail closed.
- Explicit CSV/Parquet metadata sources with root confinement, symlink rejection, file-size ceilings, and bounded CSV inference.
- A separate bounded DuckDB executor with read-only file access, conservative SQL function allowlisting, query interruption, and row/serialized-byte output ceilings (Phase 4 draft).
- Table/view and column metadata plus deterministic schema fingerprints.
- Deterministic unit tests that do not call an LLM or access a network.

Execution-derived evidence generation, planner, verifier, API, MCP server, UI, and benchmark suites remain future milestones.

## Development

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --all-groups
uv run pytest
uv run ruff check .
uv run pyright
```

## Security boundary

SQL validation is defense in depth, **not** a replacement for database-enforced read-only credentials, OS-level isolation, or network policy. The Phase 4 DuckDB executor applies per-query deadlines via DuckDB interruption, disables external access and extension loading, and bounds fetched output; its memory ceiling is not an OS process sandbox. Never connect this project to a production database using a privileged account. Query results and schema descriptions are untrusted data, not instructions.

## License

MIT. See [LICENSE](LICENSE).
