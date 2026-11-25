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
- [x] The REST golden path succeeds end to end on the shipped demo catalog:
  schema discovery → natural-language query → bounded read-only execution →
  persisted evidence → explicit human-authored decision submission →
  DecisionTrace listing and replay (PR #57).
- [ ] CLI, REST, and browser UI communicate success, abstention, validation
  failure, timeout, and unavailable-model states consistently.
  - [x] Browser UI maps timeout, worker, and storage errors to user-facing
    messages; CLI parity and the full abstention/validation matrix remain open (PRs #75, #77).
- [ ] Persian/English and RTL behavior are tested in the browser UI, not only
  in isolated formatting helpers.
  - [x] Chromium covers language switching, document `lang`/`dir`, query,
    decision, trace history, and timeout states; visual/viewport review remains open (PR #77).

### Runtime operations

- [ ] Startup validates required configuration and fails with actionable
  messages; credentials are never passed as command-line values or logged.
  - [x] REST bind/port/token/TLS settings and TLS file loading are validated
    before service databases are created (PR #80).
- [x] Health and readiness checks distinguish process liveness from usable
  catalog/database/model dependencies; Ollama readiness checks the exact
  configured tag through a one-second metadata probe (PRs #52, #81). This does
  not prove successful inference.
- [ ] Graceful shutdown stops accepting new work and safely terminates or
  drains supervised workers within a bounded deadline.
  - [x] Active handlers are counted and the warning threshold is checked
    before blocking request-thread joins; hard cancellation/deadline remains open (PR #76).
- [ ] Logs are structured, bounded, and redact credentials and sensitive query
  payloads by default.
  - [x] REST request and failure events use fixed JSON fields and redact
    query/error details; non-REST subsystem logging remains open (PRs #62, #65, #75).
- [ ] SQLite busy/locked errors, disk pressure, corrupt journals, query
  timeouts, and worker crashes have actionable operator signals.
  - [x] REST distinguishes SQLite contention, storage-full, query timeout,
    worker failure, and unexpected errors (PR #75).
  - [x] On-demand read-only `d2d doctor` reports filesystem capacity for all
    application data paths and exits non-zero at critical pressure (PR #84);
    background disk alerts and corrupt-journal signals remain open.
- [ ] Query-history and journal data lifecycle/retention behavior is explicit;
  no implicit deletion is allowed.
  - [x] Query history now retains all records by default; automatic pruning is
    opt-in through `--history-max-records N`.
  - [x] Explicit data lifecycle and backup-scope policy is documented in
    [the data lifecycle runbook](operations/data-lifecycle.md); full-application
    backup/restore and coordinated recovery remain open.
  - [ ] Evidence/DecisionTrace journal pruning remains disabled; source-data
    deletion and retention are controlled by the deployment owner.

### Backup, restore, and upgrades

- [x] A paired journal backup is restored into a fresh destination in a repeatable drill (PR #64).
- [x] The restored evidence and decision-trace journals independently verify
  and historical trace replay succeeds against the restored evidence store (PRs #63–64).
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


### REST shutdown and request logging implementation boundary

The REST server now records bounded JSON request events using only method,
normalized route, response status, timestamp, and duration. Request bodies,
questions, authorization headers, trace identifiers, and arbitrary URL paths
are not included. The CLI handles SIGINT and SIGTERM by stopping the accept
loop and waiting for active request handlers before closing shared service
resources.

The configured `--shutdown-drain-warning-seconds` is a warning threshold, not
a hard termination deadline: if requests remain active, shutdown logs a warning
and continues waiting rather than closing databases underneath live handlers.
A truly bounded shutdown remains open until query/model execution can be
cancelled and every supervised child worker is proven to terminate on deadline.


### Paired journal backup and restore

The operator workflow is available through `d2d journal backup` and
`d2d journal restore`. See
[`docs/operations/paired-journal-restore.md`](operations/paired-journal-restore.md).
This is a journal-only backup: catalog/source data, query history, config,
secrets, and model artifacts remain separate. The commands do not rotate or
prune active journals. The REST shutdown drain threshold remains a warning,
not a hard deadline; the bounded shutdown acceptance item is still open.
