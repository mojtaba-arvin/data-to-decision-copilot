# Data lifecycle and backup boundary

## Scope

The current operator backup command covers exactly two append-only SQLite
journals: Evidence and DecisionTrace. It does **not** back up the DuckDB
catalog, original CSV/Parquet/source files, query-history database, application
configuration, deployment secrets, or Ollama model artifacts. A successful
paired-journal backup must never be described as a complete application backup.

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
| Evidence journal | `d2d journal backup` | Run and verify paired backup |
| DecisionTrace journal | `d2d journal backup` | Run and verify paired backup |
| DuckDB catalog | Not included | Snapshot and retain separately |
| Original source files / object-store inputs | Not included | Retain immutable/versioned source snapshots |
| Query-history SQLite database | Not included | Back up separately or explicitly accept its loss |
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

The current CLI does not provide a complete application backup or a coordinated
full-application restore. Until that workflow exists and is tested, operators
must not claim point-in-time application recovery. Loss of the query-history
database may remove convenience history without invalidating the independently
verified Evidence and DecisionTrace journals; loss of the catalog or source
snapshot can prevent reproducing the original analytical context.

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

A future full-recovery milestone must provide and test:

1. A documented inventory and versioned manifest for all required artifacts.
2. Coordinated backup/restore procedures with explicit quiescence semantics.
3. Integrity verification for each artifact and cross-reference validation.
4. A fresh-environment restore drill that verifies query reproducibility where
   the original source/model dependencies are available.
5. Explicit RPO/RTO targets and operator-visible failure reporting.

Do not add automatic deletion, journal rotation, or retention expiry as a
substitute for this work. Disk pressure must produce an actionable alert and
controlled ingestion failure, never silent audit-data loss.
