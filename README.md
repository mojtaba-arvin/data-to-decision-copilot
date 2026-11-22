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
- Phase 8 durable SQLite decision-trace journal with transactional sequencing, append-only guards, hash-chain verification, and evidence-aware historical replay.
- Phase 9 provider-independent decision planner protocol, content-addressed planner/policy identities, deterministic policy checks, numeric literal grounding, and policy-gated trace persistence.
- Phase 10 schema-grounded typed query plans, parameterized filters, SQL AST validation, bounded read-only execution, and execution-derived evidence persistence.
- Phase 11 constrained offline semantic grammar, exact-plan regression evaluation, and optional evidence-to-decision orchestration through Phase 9 policy enforcement.
- Phase 12 injectable model-provider boundary that accepts only bounded strict JSON QueryPlans; model output never becomes SQL.
- Phase 13 local Ollama provider using JSON Schema output, bounded non-streaming generation, finite timeouts, and loopback-only HTTP. No hosted API or SDK is required.
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

## Local Ollama smoke test
Install Ollama and pull a local model before running this optional check:
```bash
ollama pull qwen3:8b
ollama serve
```
In another terminal, run the Python snippet below from the repository root:
```python
from d2d.connectors.duckdb import DuckDBConnector
from d2d.core.model_planning import ModelBackedQueryPlanner, ModelProviderIdentity
from d2d.core.ollama_provider import OllamaStructuredPlanProvider

provider = OllamaStructuredPlanProvider(model="qwen3:8b", timeout_seconds=60)
planner = ModelBackedQueryPlanner(
    provider=provider,
    provider_identity=ModelProviderIdentity(
        provider="ollama", model="qwen3:8b", revision="local-tag-qwen3-8b"
    ),
)
# Pass planner to QueryPlanningRuntime.run(question, planner), using a configured
# DataSource, DuckDBQueryExecutor, and SQLiteEvidenceStore as usual.
```
The configured tag is descriptive metadata, not a cryptographic model digest. The adapter only accepts loopback HTTP and does not download models or retry failed requests. Automated CI tests mock HTTP and do not claim real inference.

## Security boundary
SQL validation is defense in depth, not a replacement for database-enforced read-only credentials, OS-level isolation, or network policy. The DuckDB memory ceiling is not an OS process sandbox. Never connect this project to production using a privileged account.

Journal limitations and operating guidance are documented in [Spec 0007](docs/specs/0007-evidence-journal.md) and [ADR 0007](docs/adr/0007-evidence-journal.md). Decision lineage and verification are specified in [Spec 0008](docs/specs/0008-decision-traceability.md) and [ADR 0008](docs/adr/0008-decision-traceability.md). Durable trace storage and historical replay are specified in [Spec 0009](docs/specs/0009-decision-trace-persistence.md) and [ADR 0009](docs/adr/0009-decision-trace-persistence.md). Planner orchestration and policy enforcement are specified in [Spec 0010](docs/specs/0010-planner-runtime-policy.md) and [ADR 0010](docs/adr/0010-planner-runtime-policy.md). Schema-grounded query planning is specified in [Spec 0011](docs/specs/0011-schema-grounded-query-planning.md) and [ADR 0011](docs/adr/0011-typed-query-plan-boundary.md). Constrained offline semantic planning and explicit query-to-decision integration are specified in [Spec 0012](docs/specs/0012-constrained-semantic-planning.md) and [ADR 0012](docs/adr/0012-constrained-semantic-planning.md). The model-to-typed-query-plan boundary is specified in [Spec 0013](docs/specs/0013-model-query-planner-boundary.md) and [ADR 0013](docs/adr/0013-model-query-planner-boundary.md). The local Ollama structured-plan provider is specified in [Spec 0014](docs/specs/0014-ollama-provider.md) and [ADR 0014](docs/adr/0014-ollama-provider.md). Offline wheelhouse preparation and local model deployment are documented in [Offline Installation](docs/offline-install.md).

## Quality and security checks

```bash
uv run pytest tests/test_benchmark.py -q
uv run pytest --cov=d2d --cov-report=term-missing
uv run --with pip-audit pip-audit --skip-editable
uv build
```

The benchmark uses exact QueryPlan matches for a small Persian/English grammar;
it is not a claim of general NL-to-SQL accuracy. Prompt-injection and SQL bypass
regressions run in normal CI. Dependency vulnerability auditing and wheel/sdist
build checks are also CI gates. See [SECURITY.md](SECURITY.md) for trust boundaries,
deployment constraints, and known limitations.

## License
MIT. See [LICENSE](LICENSE).


## Examples and release

A synthetic sales catalog and runnable query walkthrough are available in
[examples/](examples/README.md). Use [the release checklist](docs/release-checklist.md)
to verify the exact candidate commit before publishing artifacts.
