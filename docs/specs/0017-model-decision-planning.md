# Spec 0017: opt-in model-backed decision plan contract

## Status

Engineering contract prepared; autonomous generation is **blocked** by Issue #31.
No provider is enabled or wired into the default runtime. The current 26-case
semantic fixture is development-only, not human-reviewed, not independently
held out, and not release-eligible.

## Boundary

A future provider implements a narrow structured-generation interface. The
planner accepts only an explicit user goal, explicit user constraints, and
Evidence IDs. It verifies the append-only Evidence journal itself, resolves each
requested record, and projects bounded evidence only after successful verification. The projection includes evidence IDs, source/schema provenance,
query/result digests, execution timestamp, columns, and captured rows. It never
includes SQL text, a SQL executor, database handle, tool interface, credentials,
or write capability.

The prompt labels all goal, constraint, schema, and row content as untrusted
data. Prompt-injection resistance is defense in depth: no instruction in those
fields can grant tools or expand the context boundary.

## Structured output

The provider returns one JSON object matching the strict, closed
`GeneratedDecisionPlan` schema:

- one decision with action, rationale, evidence IDs, 1–4
  alternatives with trade-offs, 1–5 reversal conditions, and 1–8 caveats;
- 1–16 uniquely identified claims, each with a statement, claim kind, and
  one or more evidence IDs;
- no unknown fields, extra prose, arbitrary SQL, or undeclared identifiers.

Strings, collections, rows, columns, cells, prompt bytes, output bytes, and
provider timeout are bounded in code. The adapter performs no retries.

## Verification and abstention

Generation is denied unless explicitly enabled and the configured semantic
verifier declares a release-eligible profile. The shipped code does not provide
such a verifier. A test double can exercise the interface but is not evidence
of calibration or release readiness.

The model does not provide a confidence score; the compatibility adapter assigns only conservative `medium` confidence and does not treat it as calibrated. Every claim must be semantically `supported` against its exact evidence
references. `contradicted`, `unknown`, verifier timeout/failure, malformed
JSON, oversized output/context, hallucinated evidence IDs, numeric values absent
from evidence, truncated evidence, and deterministic policy violations all
produce abstention. The existing deterministic numeric, provenance, truncation,
and policy checks remain independent gates; a semantic model cannot override
them.

Offline/default behavior is deterministic abstention. No failed attempt creates
or persists a DecisionTrace.

## Provenance

A successful generation result carries provider and model revision, SHA-256
prompt digest, planner artifact SHA-256, content-addressed policy reference,
semantic verifier identity/version, and evaluation profile. A persistence
integration must bind these exact fields into the immutable trace before it can
be enabled; caller-supplied free-form labels are not deployment attestation.

## Limits and release gate

The current module is an isolated contract seam, not a production feature or
release approval. Issue #31 still requires two independent fluent reviewers,
adjudication, a frozen independent holdout, predeclared thresholds, and
per-language/per-category precision, recall, abstention, and calibration
reporting. Do not set `release_eligible=true` based on development fixtures,
unit tests, schema validity, model confidence, or a successful provider call.
