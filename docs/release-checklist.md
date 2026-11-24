# Release checklist

A release is blocked until every applicable item below is verified against the
exact candidate commit. A green check on an earlier head SHA is not sufficient.

## Source and integration

- [ ] Confirm the candidate is based on the intended integration branch and includes the reviewed dependency chain.
- [ ] Review the complete diff against the release base; resolve all merge conflicts.
- [ ] Confirm no credentials, production data, local databases, model blobs, or generated artifacts are committed.
- [ ] Confirm package version, README status, changelog, license, and release notes agree.

## Quality and behavior

- [ ] Ruff passes.
- [ ] Pyright strict passes.
- [ ] Full pytest suite and coverage report pass.
- [ ] Query-to-evidence end-to-end scenario passes on a synthetic catalog.
- [ ] Query-to-decision policy and trace persistence/replay tests pass.
- [ ] CLI installed-wheel smoke passes when the CLI is part of the candidate.
- [ ] REST, MCP stdio, and browser UI contract tests pass when interfaces are included.

## Security and data integrity

- [ ] SQL AST adversarial tests reject writes, external file/table functions, multi-statements, pragmas, and extension operations.
- [ ] Prompt-injection regression tests fail closed at the model-to-QueryPlan boundary.
- [ ] CSV/Parquet path, symlink, file-size, and malformed-input tests pass.
- [ ] Evidence and DecisionTrace tamper-detection tests pass.
- [ ] Remote REST binding remains disabled until authentication, authorization, transport security, quotas, and tenant isolation are implemented.
- [ ] Query-history retention and sensitive-question storage are documented for the deployment.
- [ ] Dependency vulnerability audit has no unreviewed findings.

## Packaging and operations

- [ ] Wheel and source distribution build from the exact candidate commit.
- [ ] Offline wheelhouse installation is verified on the target OS/architecture/Python version.
- [ ] If Ollama is used, the exact local model tag and real-inference smoke result are recorded separately from mocked CI.
- [ ] Benchmark results record row count, iterations, platform, Python, DuckDB, and model metadata where applicable.
- [ ] DuckDB execution is deployed with OS/container isolation for untrusted workloads; DuckDB memory limits are not treated as an OS sandbox.
- [ ] Release artifacts and their SHA-256 hashes are retained.

## Approval and publication

- [ ] Required human review and protected-branch checks are satisfied; never fabricate reviews or bypass rulesets.
- [ ] Merge only after all required checks are green on the current head SHA.
- [ ] Tag the exact merged commit and publish the matching artifacts.
- [ ] Verify the published install instructions against the released artifacts.


## Resource and journal-growth measurements

The synthetic benchmark reports `peak_rss_bytes` using the operating system's
process high-water RSS counter. This is the process-wide maximum, includes setup
and warm-up, and is not an isolated DuckDB allocation measurement.

The `evidence_journal` report records committed evidence row count and SQLite
main-database file size after the measured query loop. It does not imply a
retention or compaction policy: both journals remain append-only, and safe
compaction still requires a verifiable checkpoint and backup design. WAL
sidecars are not included in this size.
