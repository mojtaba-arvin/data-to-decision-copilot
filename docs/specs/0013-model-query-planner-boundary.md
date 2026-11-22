# Spec 0013: strict model-to-query-plan boundary

## Goal

Allow an injected model/provider adapter to propose a typed QueryPlan without granting it SQL construction or execution authority.

## Contract

- The provider receives the user question, bounded discovered schema metadata, the QueryPlan JSON Schema, and a fixed output-byte ceiling.
- Question, schema-context, and provider-response sizes are bounded.
- Provider output must be one strict JSON object. Markdown, free-form SQL, duplicate keys, non-finite JSON numbers, extra fields, invalid Pydantic shapes, and foreign schema fingerprints are rejected.
- The adapter returns QueryPlan only. QueryPlanningRuntime remains responsible for fresh schema discovery, identifier resolution, SQL compilation, AST validation, bounded read-only execution, and Evidence persistence.
- Provider calls are synchronous at this boundary. A concrete provider adapter must enforce its own network timeout, retry budget, token budget, privacy policy, and credential handling.

## Identity and provenance

The planner identity hashes the declared provider/model/revision configuration and fixed prompt-contract version. This is a configuration fingerprint, not a signed model-weight digest, remote deployment attestation, or proof that a provider served the declared revision. Production adapters must document how the declared revision is pinned and independently verifiable.

## Security properties

- Model output is untrusted data, never executable SQL.
- Schema identifiers and question text are explicitly separated in the prompt.
- The model sees metadata only, not table rows or credentials.
- Unknown identifiers are rejected by the existing schema-grounded compiler before execution.
- The adapter cannot raise the hard row cap or bypass the SQLGlot/read-only executor.

## Limitations

No provider SDK, API key, network call, local model runtime, or paid inference was added. This phase supplies a strict adapter contract and validation boundary; it does not claim that a real LLM was invoked. Provider timeout, cancellation, rate limiting, data residency, and remote model provenance remain provider-adapter responsibilities.
