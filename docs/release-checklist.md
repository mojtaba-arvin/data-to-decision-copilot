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


## Remote REST authentication boundary

Remote REST binds require all three of: a bearer token supplied through the
D2D_REST_BEARER_TOKEN environment variable (minimum 32 characters), a PEM
TLS certificate, and its private key. The CLI accepts --tls-certfile and
--tls-keyfile; it never accepts the secret as a command-line argument. TLS
is enforced by the server with TLS 1.2 or newer, and all HTTP routes require the
same bearer credential. Keep the service behind a network firewall and rotate
credentials through the deployment secret manager. This is single-credential
service authentication, not per-user identity, role-based authorization, or
tenant isolation; do not expose it as a multi-tenant service.


## Verified journal backups (E9 groundwork)

Evidence and DecisionTrace journals support consistent SQLite online backups.
A backup is accepted only after the source journal verifies, is copied through
SQLite's backup API, and the copy independently verifies. Publication is atomic
and refuses to overwrite an existing backup. The report records the backup
SHA-256, byte size, record count, and chain-head hash. This does not delete,
compact, or expire journal entries; retention remains intentionally disabled
until an explicit audit-retention policy and restore/anchor procedure are approved.


## Single-tenant operational readiness

The initial operational release target is a supported **single-tenant** deployment.
This gate does not authorize hostile-catalog execution, multi-tenant access, or
autonomous decision generation.

### First-run user journey

- [ ] A new operator can install from a clean supported environment using only
  the published instructions.
- [x] A synthetic demo catalog can be created with `d2d demo` and queried without writing application code; the command refuses to overwrite existing paths.
- [ ] The documented golden path succeeds end to end:
  schema discovery → natural-language query → bounded read-only execution →
  persisted evidence → explicit human-authored decision submission →
  DecisionTrace listing and replay.
- [ ] CLI, REST, and browser UI communicate success, abstention, validation
  failure, timeout, and unavailable-model states consistently.
- [ ] Persian/English and RTL behavior are tested in the browser UI, not only
  in isolated formatting helpers.

### Runtime operations

- [ ] Startup validates required configuration and fails with actionable
  messages; credentials are never passed as command-line values or logged.
- [ ] Health and readiness checks distinguish process liveness from usable
  catalog/database/model dependencies.
- [ ] Graceful shutdown stops accepting new work and safely terminates or
  drains supervised workers within a bounded deadline.
- [ ] Logs are structured, bounded, and redact credentials and sensitive query
  payloads by default.
- [ ] SQLite busy/locked errors, disk pressure, corrupt journals, query
  timeouts, and worker crashes have actionable operator signals.
- [ ] Query-history and journal data lifecycle/retention behavior is explicit;
  no implicit deletion is allowed.

### Backup, restore, and upgrades

- [ ] A backup is restored into a fresh destination in a repeatable drill.
- [ ] The restored evidence and decision-trace journals independently verify
  and historical trace replay succeeds against the restored evidence store.
- [ ] Upgrade tests cover databases created by the previous supported release.
- [ ] Offline installation is verified on each published OS/architecture/Python
  combination, including required native dependencies.

### Deployment boundary

- [ ] Remote service remains single-tenant while shared-token authentication
  is the only remote identity mechanism.
- [ ] OS/container policy provides filesystem and network confinement before
  accepting hostile catalogs; a supervised worker process alone is not a
  complete sandbox.
- [ ] Autonomous decision generation remains disabled until the Persian/English
  semantic-verification release thresholds are met.
- [ ] Journal rotation and retention remain disabled until archive-chain
  verification, crash recovery, restore drills, redundant copies, and audited
  deletion policy are complete.
