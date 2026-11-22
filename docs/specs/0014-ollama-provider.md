# Spec 0014: local Ollama structured-plan provider

## Goal

Connect the Phase 12 typed-plan boundary to an actual local inference server without adding a hosted API, vendor SDK, or inference bill.

## Contract

- Implement `StructuredPlanProvider` using Ollama's local `POST /api/generate` endpoint.
- Send the prompt, JSON Schema response format, `stream=false`, deterministic temperature, and a bounded generation token budget.
- Accept only a completed response envelope containing a string `response`; enforce envelope and generated-output byte limits.
- Enforce a finite request timeout and perform no automatic retries.
- Use only plain HTTP to loopback IPs or the literal `localhost`. Reject remote hosts, credentials, URL paths, query strings, fragments, and HTTPS overrides.
- Keep this adapter dependency-free using Python's standard library.
- Return model text only. Phase 12 remains responsible for strict JSON and QueryPlan validation; the existing query runtime remains responsible for compilation, SQL safety, execution, and Evidence.

## Operational behavior

- The caller must have Ollama running locally and have already pulled the selected model.
- The configured model name is sent to Ollama; this adapter does not download models.
- Connection failures, timeouts, HTTP failures, malformed envelopes, incomplete generation, and oversized output raise `OllamaProviderError`.
- No retry is attempted, avoiding duplicate local inference and hidden latency.
- The loopback restriction is deliberate: schema metadata and questions must not be sent to an arbitrary remote endpoint through this adapter.

## Limitations

- Automated tests use a mocked HTTP transport; CI does not require Ollama or download model weights.
- A successful adapter test is not evidence of successful real-model inference.
- Ollama's model tag is caller-declared identity metadata, not cryptographic proof of model weights.
- The adapter is synchronous; cancellation is not available while the underlying blocking HTTP request is in progress. Timeout bounds that wait.
