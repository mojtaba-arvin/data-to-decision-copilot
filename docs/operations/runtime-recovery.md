# Full runtime recovery bundle

## Scope and preconditions

The `d2d recovery backup` command creates a versioned recovery bundle for a
stopped single-tenant runtime. Stop the REST/MCP/CLI workload and all other
writers before running it. The backup opens the DuckDB catalog with an
exclusive writable connection and checkpoints it; this fails when another
DuckDB process still owns the catalog. Keep the service stopped until the
bundle has been published. The command does not coordinate or stop external
processes that do not use this catalog.

The bundle contains exactly:

- The materialized DuckDB catalog.
- The Evidence SQLite journal.
- The DecisionTrace SQLite journal.
- The query-history SQLite database.
- A canonical versioned manifest with artifact sizes and SHA-256 digests,
  database schema versions, catalog schema fingerprint, journal heads/counts,
  package version, and sanitized provider/model/history-retention settings.

All three SQLite databases pass `PRAGMA integrity_check`; both journal chains
and every DecisionTrace-to-Evidence reference are verified. The catalog is
checkpointed, copied only after the DuckDB connection closes, and independently
opened read-only to recompute its schema fingerprint. The complete bundle is
published atomically without replacing an existing destination.

## Create a bundle

Run from the supported Python environment with the application stopped:

```bash
d2d recovery backup \
  --catalog ./analytics.duckdb \
  --evidence-db ./d2d-evidence.sqlite \
  --trace-db ./d2d-traces.sqlite \
  --history-db ./d2d-history.sqlite \
  --provider ollama \
  --model qwen3:8b \
  --output ./backups/runtime-2026-11-28
```

Omit `--provider` and `--model` for the default rule-based planner. Supply
`--history-max-records N` only when the runtime was configured with that
explicit history-retention limit. The command emits a JSON report containing
the artifact count, catalog fingerprint, journal heads/counts, and trace
cross-reference count. It never rotates, prunes, or rewrites active journals.

## Restore to a new directory

The destination must not exist:

```bash
d2d recovery restore \
  --bundle ./backups/runtime-2026-11-28 \
  --output ./recovered/runtime
```

Restore validates the manifest digest, exact artifact inventory, sizes,
SHA-256 digests, SQLite integrity/schema, journal chains, trace/evidence
references, and read-only catalog schema fingerprint before atomically
publishing the four databases. On any failure, the destination is not
published. The report returns the sanitized provider/model/retention settings;
use those settings when reconstructing the service:

```bash
d2d serve \
  --catalog ./recovered/runtime/catalog.duckdb \
  --evidence-db ./recovered/runtime/evidence.sqlite \
  --trace-db ./recovered/runtime/decision-traces.sqlite \
  --history-db ./recovered/runtime/query-history.sqlite \
  --provider ollama --model qwen3:8b
```

Re-provision and verify Ollama separately if it is used. Do not copy secrets
into shell history; supply the REST bearer token through the configured
environment variable and TLS files through protected deployment paths.

## Exclusions and operational limits

The bundle is a local integrity-checked recovery artifact, not an encrypted,
signed, off-host archive service. Protect it with OS access controls, encrypt
it at rest, and copy it to a separate host using the organization's approved
backup system. SHA-256 detects accidental changes relative to the manifest;
it is not a detached authenticity signature against an attacker who can
rewrite both files and manifest.

The bundle deliberately excludes original CSV/Parquet/source snapshots,
source-system credentials, REST bearer tokens, TLS private keys, host/network
configuration, secret-manager state, and Ollama model weights. Retain immutable
source snapshots and checksums separately when catalog rebuildability or
regulatory reproducibility is required. Restore does not provide a globally
atomic snapshot of external systems. RPO/RTO targets and a customer-environment
restore drill remain deployment-specific release gates.

This recovery workflow does not change the shared-token security model:
remote REST remains trusted single-tenant only. It does not establish Issue
#33 tenant isolation, Issue #34 hostile-catalog sandboxing, or Issue #31
autonomous recommendation readiness. Journal rotation, pruning, and automatic
deletion remain disabled.
