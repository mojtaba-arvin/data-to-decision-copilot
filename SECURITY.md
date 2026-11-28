# Security model and limitations

## Intended deployment

Data-to-Decision Copilot is a local, read-only analytics tool. The REST server
binds to loopback by default. Remote binds are gated by TLS and a 32+ character
shared bearer token; this is not per-user authentication, RBAC, tenant isolation,
or a substitute for a hardened reverse proxy. The MCP server uses stdio. Keep
remote deployments single-tenant and trusted until identity, authorization,
quotas, and tenant isolation are implemented.

## Trust boundaries

- User questions, schema names, and model responses are untrusted input.
- A model may return only a bounded, strict JSON QueryPlan. It never receives a
  SQL execution capability and cannot write to the evidence journal.
- QueryPlan identifiers are resolved against a current schema fingerprint.
  Values are bound parameters. SQLGlot validation and the DuckDB executor apply
  independent read-only and resource limits.
- CSV/Parquet inputs must be explicitly listed under a configured root. Symlinks,
  path traversal, unsupported formats, per-file oversize inputs, and excessive
  aggregate input size are rejected.
- Decision claims are typed as Fact, Inference, or Recommendation. Fact grounding
  is a conservative scalar-literal match (including Persian/Arabic-Indic digit
  normalization) plus narrow same-template conflict detection. It is not general
  semantic entailment, calibrated confidence, unit conversion, or robust
  contradiction detection. Human review remains necessary for consequential decisions.

## Operational limits

- The default executor runs DuckDB in the service process and is intended only
  for trusted local catalogs. On POSIX, `--isolated-worker` opts into a fresh,
  supervised child process for each query. The parent enforces a wall-clock
  deadline, bounded IPC, and process-group termination; the worker applies CPU,
  address-space, file-descriptor, and file-size limits before importing DuckDB.
- The supervised worker is process-level isolation, not a complete OS sandbox.
  It does not independently deny network egress or confine filesystem reads.
  Deployments handling untrusted catalogs must add OS/container controls: mount
  only required catalog inputs read-only, deny network egress, and restrict access
  to unrelated filesystem paths. The flag is POSIX-only and remains opt-in.
- A schema fingerprint re-check detects schema changes during planning, but it
  is not a cross-process transaction or snapshot-isolation guarantee.
- SQLite hash chains detect accidental or partial tampering; they do not prevent
  an attacker with full database-file and process access from rewriting the
  entire journal. Higher assurance requires external anchoring or signatures
  with separately protected keys.
- Query history is stored in a local SQLite database and persists questions and
  evidence metadata, but not result rows or raw SQL bind values. It is retained
  without automatic pruning by default; an explicit `--history-max-records N`
  enables bounded retention. Questions may themselves contain sensitive
  information; protect the history database and apply only an explicitly
  approved retention policy.
- Query history is not a substitute for the independently verified Evidence
  journal and DecisionTrace audit stores.
- `d2d recovery backup` creates a local integrity-checked bundle of the
  materialized DuckDB catalog and all three SQLite databases while the serving
  process is stopped. It excludes source snapshots, secrets, TLS keys, host
  configuration, and model weights. The bundle is not encrypted, signed, or
  automatically copied off-host; deployment operators must protect it.
- Evidence and DecisionTrace journals are append-only and currently have no
  automatic pruning/compaction policy. Monitor disk growth and plan verified
  archival/rotation before long-running or high-volume deployments; do not delete
  journal rows in place because doing so invalidates sequence/hash-chain checks.
- The Ollama adapter accepts loopback HTTP only. Model tags are descriptive
  identifiers, not cryptographic model provenance.

## Reporting vulnerabilities

Do not include production data, credentials, database files, or private schema
details in public issues. Provide a minimal reproduction using synthetic data.
