# Data lifecycle and backup boundary

## Scope

The operator has two distinct recovery workflows. `d2d journal backup`
covers exactly the Evidence and DecisionTrace journals. The newer
`d2d recovery backup` workflow covers the materialized DuckDB catalog and all
three SQLite databases (Evidence, DecisionTrace, and query history), with a
versioned integrity manifest. Neither workflow includes original
CSV/Parquet/source files, deployment secrets, TLS keys, host configuration, or
Ollama model artifacts. A paired-journal backup must never be described as a
complete application backup.

## Retention defaults

- Query history is retained without automatic pruning by default. An operator
  may explicitly opt into bounded history with
  `--history-max-records N`; this is a product-level storage trade-off and
  should be recorded in the deployment runbook.
- Evidence and DecisionTrace are append-only audit records. Automatic journal
  pruning and rotation are not implemented and must remain disabled.
- The application does not define a universal expiry period for source data.
  The data owner must apply the organization's approved retention and deletion
  policy to source files and catalogs outside the application.
- Do not delete a catalog or source file merely because a query-history row or
  journal entry is old. Preserve the relationship between evidence, trace,
  catalog version, and source snapshot for the period required by the business
  and regulatory policy.

## Required operator backup inventory

Before treating a deployment as recoverable, the operator must inventory and
protect each item independently:

| Data or state | Current coverage | Operator responsibility |
| --- | --- | --- |
| Evidence journal | `d2d recovery backup` (or paired journal backup) | Verify bundle and journal chain |
| DecisionTrace journal | `d2d recovery backup` (or paired journal backup) | Verify bundle, chain, and evidence references |
| DuckDB catalog | `d2d recovery backup` | Stop service; verify catalog fingerprint |
| Query-history SQLite database | `d2d recovery backup` | Verify SQLite integrity and schema |
| Original source files / object-store inputs | Not included | Retain immutable/versioned source snapshots |
| Configuration and deployment metadata | Not included | Version non-secret configuration |
| Secrets and TLS private keys | Not included | Back up/rotate through a secret manager; never commit them |
| Ollama model files and runtime | Not included | Re-provision and verify the exact model independently |

Keep backups access-controlled and encrypted at rest. Store at least one
verified copy separately from the host that runs the service. Test restoration
to a fresh location; a successful copy or checksum alone is not a restore
drill.

## Consistency and recovery limitations

The paired journal backup snapshots two independent SQLite databases and
validates cross-journal references. It is not a globally atomic snapshot across
the catalog, source data, query history, and both journals. If a business
recovery point must align all of these artifacts, quiesce writes and coordinate
the snapshots at the deployment layer, recording the selected catalog/source
version and backup timestamps.

The CLI now provides a versioned runtime recovery bundle through
`d2d recovery backup` and `d2d recovery restore`; see the
[full runtime recovery runbook](runtime-recovery.md). Backup requires the
serving workload to be stopped and the operator to prevent restart until
publication completes. The catalog is checkpointed under an exclusive DuckDB
writer connection; all three SQLite files are independently integrity-checked.
Restore verifies all four artifacts and publishes into a fresh directory
without replacement. The customer-environment restore drill, RPO/RTO targets,
off-host encryption/signing, and source/model re-provisioning remain separate
operator/release responsibilities.

## Runtime recovery design

The implementation contract for a runtime recovery bundle is recorded in
[ADR 0016](../adr/0016-operational-recovery-and-bounded-shutdown.md). It covers
the DuckDB catalog and all three SQLite databases (Evidence, DecisionTrace,
and query history), with a versioned manifest, per-artifact digests, journal
cross-reference verification, catalog schema verification, staged restore,
and atomic no-replace publication.

Original CSV/Parquet inputs are provenance/rebuild dependencies, not serving
dependencies after materialization into the DuckDB catalog. Keep immutable
source snapshots and their versions/checksums in the source system's backup
policy whenever rebuilding the catalog or regulatory reproducibility is
required. Secrets, TLS private keys, host configuration, and Ollama model
weights remain external dependencies and must never be copied into the bundle.
A runtime recovery bundle must not be described as preserving these excluded
items.

## Release gate

The remaining full-recovery acceptance work is:

1. Execute a clean deployment-environment restore and re-query drill.
2. Record deployment-specific RPO/RTO targets and operator-visible outcomes.
3. Validate off-host encryption, access controls, and detached authenticity
   according to the deployment's backup policy.
4. Re-provision and verify excluded source/model dependencies independently.

Do not add automatic deletion, journal rotation, or retention expiry as a
substitute for this work. Disk pressure must produce an actionable alert and
controlled ingestion failure, never silent audit-data loss.
