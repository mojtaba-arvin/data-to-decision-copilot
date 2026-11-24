# Spec 0007: durable evidence journal and verification

## Scope
Persist Phase 5 execution-derived evidence in a local SQLite journal. Records are append-only through database triggers, assigned a monotonic sequence, and linked by SHA-256 hashes. Verification independently recomputes evidence, record, and chain digests.

## Record and chain
- Persist the complete JSON representation of the immutable Evidence contract.
- Validate query/result digests against stored SQL and captured output before append and during verification.
- Compute a canonical record digest, then an entry digest over schema version, sequence, evidence ID, record digest, and previous entry digest.
- Use an immediate transaction to serialize concurrent writers and make sequence allocation plus insert atomic.
- Enforce unique evidence IDs and block UPDATE/DELETE with SQLite triggers.
- Expose append, get, count, and verify; no public update/delete API.

## Versioning and operations
The journal stores a schema version and fails closed on an unknown version. Schema evolution requires an explicit migration. SQLite is the initial local persistence adapter, not a distributed production store.

## Security limitations
The hash chain detects record alteration, deletion, reordering, and partial rewriting when checked against an independently retained chain head. It does not stop an attacker with full database/schema control from dropping triggers and rebuilding the entire chain. No signing key, external anchor, encryption-at-rest, or multi-host coordination is provided. Protect the database file and backups with OS-level access controls. Raw SQL bind parameters remain excluded.
