# Security model and limitations

## Intended deployment

Data-to-Decision Copilot is a local, read-only analytics tool. The REST server
binds to loopback only. The MCP server uses stdio. Do not expose either interface
to an untrusted network without adding authentication, authorization, transport
security, request quotas, and tenant isolation.

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
- Decision claims are typed as Fact, Inference, or Recommendation. Literal fact
  grounding is a conservative value-match check, not a general proof of semantic
  entailment. Human review remains necessary for consequential decisions.

## Operational limits

- DuckDB limits are not an operating-system sandbox. Run untrusted workloads in
  a restricted process/container with filesystem and CPU/memory limits.
- A schema fingerprint re-check detects schema changes during planning, but it
  is not a cross-process transaction or snapshot-isolation guarantee.
- SQLite hash chains detect accidental or partial tampering; they do not prevent
  an attacker with full database-file and process access from rewriting the
  entire journal. Higher assurance requires external anchoring or signatures
  with separately protected keys.
- Query history is stored in a bounded local SQLite database (default maximum
  1,000 records). It persists questions and evidence metadata, but not result rows
  or raw SQL bind values. Questions may themselves contain sensitive information;
  protect the history database and remove it, including SQLite WAL/SHM sidecars
  when the service is stopped, when its retention period ends.
- Query history is not a substitute for the independently verified Evidence
  journal and DecisionTrace audit stores.
- Evidence and DecisionTrace journals are append-only and currently have no
  automatic pruning/compaction policy. Monitor disk growth and plan verified
  archival/rotation before long-running or high-volume deployments; do not delete
  journal rows in place because doing so invalidates sequence/hash-chain checks.
- The Ollama adapter accepts loopback HTTP only. Model tags are descriptive
  identifiers, not cryptographic model provenance.

## Reporting vulnerabilities

Do not include production data, credentials, database files, or private schema
details in public issues. Provide a minimal reproduction using synthetic data.
