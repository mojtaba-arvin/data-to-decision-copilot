# ADR 0016: operational recovery bundle and bounded REST shutdown

## Status

Accepted design; implementation is not complete. Issue #50 remains open.

## Context

The current `d2d journal backup/restore` command protects only the Evidence and
DecisionTrace SQLite journals. The running application also depends on a
DuckDB catalog and a separate SQLite query-history database. The supported
CLI reconstructs the serving runtime from these four data files plus explicit
startup configuration. It does not currently expose the file-source connector
as a serving CLI input.

The REST server uses non-daemon request threads and shares application stores
between handlers. Its drain threshold is warning-only. Python cannot safely
force-stop an individual thread; calling `server_close()` still waits for
non-daemon handlers. Closing shared stores while a handler is running would
risk corruption and use-after-close failures.

## Decision: recovery scope

Implement a versioned, integrity-checked **runtime recovery bundle** containing:

1. DuckDB catalog database, checkpointed and copied only while the service is
   stopped and no process has it open.
2. Evidence journal SQLite online snapshot.
3. DecisionTrace journal SQLite online snapshot.
4. Query-history SQLite online snapshot.
5. Canonical manifest with format version, application/package version,
   supported database schema versions, artifact names/sizes/SHA-256 digests,
   catalog schema fingerprint, journal heads/record counts, and sanitized
   runtime configuration needed to reconstruct the service (provider/model,
   history-retention mode, and logical artifact roles).

The backup command must fail closed if it cannot establish quiescence, checkpoint
and validate the catalog, obtain valid SQLite snapshots, or durably publish the
complete bundle. It must never rotate, prune, or rewrite the live journals.

Restore must stage into a fresh directory, validate the manifest and every
artifact digest, run SQLite `integrity_check` on all three databases, verify
both append-only journal chains and every DecisionTrace-to-Evidence reference,
open the DuckDB catalog read-only and recompute its schema fingerprint, then
publish the complete directory atomically without replacing an existing path.
History is convenience metadata; it must still pass SQLite integrity checks.

## Explicit external dependencies and exclusions

- Original CSV/Parquet inputs are not required to serve queries after they have
  been materialized into the DuckDB catalog. Their immutable source snapshots,
  source-system versions, and checksums remain separately managed provenance
  dependencies when catalog rebuildability or regulatory reproducibility is
  required. The runtime bundle must not claim to preserve those original inputs.
- Secrets, TLS private keys, bearer tokens, host/network configuration, and
  secret-manager state must never be embedded in the bundle. Operators restore
  these from their secret manager and reconfigure them.
- Ollama model weights are external runtime dependencies. Record provider/model
  identity in the manifest, but re-provision and verify the model separately.
- This is a single-tenant local recovery artifact, not an encrypted, signed,
  off-host archive service. Access control, encryption at rest, off-host copies,
  and detached authenticity signatures remain deployment responsibilities or
  later release gates.

A successful runtime restore means the application data files can be opened and
the journal/evidence relationships verified. It does not claim original-source
rebuildability, secret recovery, model availability, or a globally atomic
snapshot of external systems.

## Decision: hard-bounded shutdown

Do not attempt to kill request threads, make handler threads daemon-only, or
close shared stores underneath active requests.

Move the REST serving runtime behind a process-supervision boundary. A parent
supervisor owns the hard shutdown deadline; a child process owns the HTTP server,
request threads, DuckDB connection, and SQLite stores. On shutdown, the child
stops accepting new work and drains within its graceful interval. The parent
waits until the configured absolute deadline and then terminates the entire
child process boundary, including its request threads and in-process resources.
The supervisor reports whether shutdown was graceful or forced.

The implementation must also account for isolated query-worker descendants:
process-group/job-object containment must ensure forced shutdown does not leave
orphan workers. Do not claim a cross-platform hard deadline until the relevant
POSIX process-group and Windows Job Object behavior is implemented and tested.
If a platform cannot guarantee descendant termination, fail startup or clearly
mark bounded shutdown unsupported on that platform rather than weakening the
guarantee.

## Required implementation sequence

1. Add recovery-bundle format, manifest validation, catalog checkpoint/copy,
   SQLite history snapshot, and artifact verification primitives.
2. Add failure-injection tests for partial snapshots, corrupt/missing/extra
   artifacts, manifest tampering, disk-full, destination races, and restore
   publication failures.
3. Add CLI backup/restore commands and a fresh-directory restore/re-query drill.
4. Add the process supervisor and child lifecycle protocol without changing the
   current shared-store threading model inside the child.
5. Add graceful-drain, deadline-expiry, worker-descendant, signal, and
   supervisor-crash tests on each claimed supported OS.
6. Update the release checklist only when exact-head CI and restore/shutdown
   drills prove the corresponding acceptance criteria.

## Non-goals and safety invariants

- No automatic journal rotation, pruning, or retention expiry.
- No non-genesis checkpoint advancement as part of application backup.
- No unsafe thread termination or closing shared stores under live handlers.
- No secrets or private keys in manifests or archives.
- No multi-tenant, hostile-catalog, or autonomous-decision capability is implied.
