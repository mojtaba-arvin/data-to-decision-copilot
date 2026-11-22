# Spec 0012: constrained semantic planning and decision integration

## Goal

Provide a usable offline natural-language-to-typed-plan adapter and connect query evidence to the existing policy-gated decision runtime without weakening the SQL or evidence boundaries.

## Semantic adapter

`RuleBasedSemanticPlanner` is a deterministic, explicitly limited grammar for:
- counting rows in a named discovered table;
- SUM/TOTAL/AVG/AVERAGE/MIN/MAX of a named column, optionally grouped by a named column;
- selecting an explicit comma-separated projection.

Table and column names must resolve uniquely against the current SchemaSnapshot. There is no fuzzy matching, join inference, filter inference, raw SQL, or fallback guessing. Unsupported, ambiguous, unknown, and prompt-injection-like inputs fail closed. This is not an LLM and must not be represented as one.

Planner and grammar identities are stable SHA-256 references. The adapter is offline and has no network/model dependency.

## Evaluation

The evaluation API compares exact typed QueryPlan outputs against a fixed test corpus. Unsupported/rejected inputs count separately and reduce exact-match accuracy. This is a deterministic regression metric, not a claim of broad semantic quality.

## Query-to-decision integration

QueryDecisionRuntime first runs QueryPlanningRuntime. It can return evidence-only output when no decision planner is supplied. If an explicit decision planner is supplied, only the newly persisted evidence ID is passed to Phase 9 DecisionRuntime, which verifies the journal, checks source/schema provenance, evaluates DecisionPolicy, and persists a DecisionTrace only when allowed.

No decision is inferred automatically from database rows. A decision adapter must provide an explicit proposal, and numeric grounding/truncation/reversal-condition policy remains enforced by Phase 9.

## Security and limitations

- QueryPlanner still returns typed QueryPlan only; compiled SQL, SQLGlot validation, bound parameters, DuckDB read-only execution and resource limits are unchanged.
- The supported grammar is intentionally tiny and exact; real semantic interpretation requires a separately reviewed provider adapter.
- No paid API, network call, local model runtime, joins, inferred filters, or free-form SQL is introduced.
- Evaluation exact-match scores are meaningful only for the checked-in corpus.
- Planner identity hashes identify this adapter/grammar, not a signed deployment artifact.
- Query planning and execution are not transactionally pinned to one snapshot.
