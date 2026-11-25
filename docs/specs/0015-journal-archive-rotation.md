# Spec 0015: verifiable journal archive segments and rotation

## Status

Proposed design. No journal rotation or retention deletion is implemented by this specification alone.

## Context

The evidence and DecisionTrace stores are independent SQLite databases. Each starts at sequence 1, uses a zero-hash genesis value, stores a per-record digest and a previous-entry-linked SHA-256 entry digest, and rejects ordinary UPDATE/DELETE statements. Verified SQLite backups are available, but they are full snapshots: they do not reduce active journal size, establish an externally trusted checkpoint, or authorize deletion.

Deleting rows in place, resetting sequence numbers, or copying only the newest rows would break the current verification contract. A backup file by itself is not an authenticated archive if an attacker can rewrite both the file and its adjacent metadata.

## Goals

- Preserve verifiability across archive segments and active-journal rotation.
- Never silently discard records or reset a chain to an unanchored genesis.
- Make rotation crash-safe and idempotent.
- Permit offline verification and restore without contacting a hosted service.
- Keep evidence and decision-trace archives separately typed and versioned.
- Make retention a governed archive-lifecycle operation, not a database-row deletion feature.

## Non-goals

- Distributed consensus or a centralized ledger.
- Proving who authored a record without a separately protected signing key.
- Automatic deletion based only on age or disk pressure.
- Treating a local SHA-256 digest as an external trust anchor.
- Rewriting the existing schema or changing current journal semantics in-place.

## Archive segment format

Each completed segment is an immutable directory containing:

1. A SQLite backup snapshot of exactly one journal type.
2. A canonical UTF-8 JSON manifest.
3. A detached manifest signature when signing is configured.
4. A checksum file or equivalent manifest fields for the SQLite artifact.

The canonical manifest must include:
- format and manifest schema versions;
- journal kind (`evidence` or `decision_trace`) and source journal schema version;
- repository/application build identity and archive tool version;
- segment UUID and creation timestamp in UTC;
- inclusive first/last sequence numbers and record count;
- first and last entry hashes from the segment;
- previous segment manifest digest (zero digest only for the first segment);
- the active journal checkpoint hash before and after the rotation boundary;
- SQLite snapshot byte size and SHA-256;
- verification result and verifier version;
- signing key ID and signature algorithm when signed.

Manifest serialization is canonical JSON: UTF-8, sorted keys, compact separators, no NaN/Infinity, and no implicit local-time values. The manifest digest is SHA-256 over the canonical manifest payload excluding its own digest and detached signature fields.

## Chain continuity

The first archived segment begins at sequence 1 and genesis hash zero. Each later segment must:
- start at the prior segment's last sequence + 1;
- identify the prior segment's manifest digest;
- identify the prior segment's final journal entry hash as its predecessor;
- contain a complete internally verifiable journal chain for its declared range.

The first record of a later segment must be verifiable against the prior segment head. A segment must not be accepted merely because its local SQLite database verifies from genesis. The archive verifier must verify the segment's declared starting sequence and previous-entry hash using a segment-aware verifier.

The active journal after rotation continues the same logical sequence and chain. It must not silently restart from sequence 1/genesis. If the existing SQLite schema cannot represent a non-genesis starting checkpoint, rotation must first introduce an explicit checkpoint metadata record/schema version and a migration plan. Do not simulate continuity by editing hashes after the fact.

## Rotation protocol

Rotation is a state machine with durable phases:

1. **Prepare:** acquire an exclusive per-journal rotation lock; verify the complete active journal; record its current sequence and head hash.
2. **Snapshot:** use SQLite's online backup API to produce a temporary snapshot. Do not copy the live database file directly while WAL activity may exist.
3. **Verify:** verify the snapshot, row count, first/last sequence, entry hashes, schema version, and captured head against the prepared checkpoint.
4. **Manifest:** write the canonical manifest to a temporary file; compute its digest; sign it when a configured signing key is available.
5. **Publish:** atomically publish the complete segment directory without overwriting an existing segment name. The archive directory must be on one filesystem for atomic rename semantics.
6. **Checkpoint:** commit the new active-journal checkpoint/segment reference in one SQLite transaction only after the archive segment is durable and re-verified.
7. **Resume:** release the lock and emit a structured audit event with archive ID, range, digest, and result.

If a crash occurs before publication, the active journal remains untouched and temporary files are safe to clean after validation. If a crash occurs after archive publication but before checkpoint commit, retry must detect the already-published segment by manifest digest and finish idempotently; it must not publish a duplicate or advance twice. If checkpoint commit succeeds, the archive must already be durable and independently verifiable.

## Restore and verification

A verifier must:
- validate manifest canonicalization and digest;
- validate detached signature and key ID when configured;
- verify each SQLite file checksum and schema;
- verify each segment's internal record hashes and sequence continuity;
- verify cross-segment sequence and previous-hash continuity;
- compare the active journal checkpoint with the final archive head;
- reject missing, duplicated, reordered, overlapping, or gapped segments;
- report the first failing segment, sequence, expected hash, and observed hash.

Restore must build into a new destination, verify the entire archive chain, reconstruct the active journal with its original logical sequence/head, and only then atomically publish the restored database. It must never overwrite the only valid source or archive.

## Signing and key management

Unsigned archives detect accidental corruption but do not protect against an attacker who can rewrite all archive files and manifests. Higher-assurance deployments should sign manifest digests using an offline or OS-protected key. Private keys must never be stored beside archives or in the SQLite database. The verifier must support public-key-only offline verification, key IDs, key rotation, and explicit trust-store updates. Signature absence must be visible in the report and must not be represented as authenticated provenance.

## Retention and deletion

Retention applies to complete verified segments, not individual rows. A segment may be eligible for deletion only when:
- policy explicitly authorizes its retention expiry;
- a separately stored archive copy has been verified;
- all dependent manifests/checkpoints remain available;
- the deletion event records the segment digest, range, policy, actor, and timestamp;
- deletion does not create a gap in the required verification chain.

The default policy is no automatic deletion. Disk pressure triggers an alert and blocks new ingestion safely before the journal becomes corrupted; it must not trigger silent data loss.

## Required tests before implementation is accepted

- Crash injection at every state transition.
- Concurrent append attempts during rotation and lock acquisition.
- Repeated/idempotent rotation after each crash point.
- Tampered SQLite bytes, manifest, signature, and segment order.
- Missing, duplicate, overlapping, gapped, and wrong-journal segments.
- Restore to a fresh location and full chain verification.
- Key rotation, unknown key ID, unsigned archive, and revoked key behavior.
- WAL-mode source backup during concurrent reads/writes.
- Disk-full and permission failures before and after publication.
- Active journal resumes with monotonic sequence and previous-hash continuity.
- Evidence archive and trace archive cannot be interchanged.

## Rollout

1. Implement the segment-aware verifier and manifest schema without modifying active journals.
2. Add read-only archive verification CLI and fixtures.
3. Implement crash-safe snapshot/rotation and checkpoint migration.
4. Implement restore and operational metrics.
5. Enable retention only after restore drills and external archive-copy verification are automated.

No row deletion, compaction, or automatic retention may ship before all rollout gates pass.


## Implementation increment: segment-chain verification contract

The first implementation increment adds a version-2 segment-manifest verifier in
`d2d.core.journal_chain`. It validates canonical manifest bytes and digests,
database byte size and digest, journal type and schema, unique segment IDs,
contiguous sequence ranges, first-record predecessor hashes, previous-manifest
links, and actual SQLite boundary hashes. Segments are supplied oldest first;
gaps, overlaps, and reordered segments fail closed.

Record-level validation is deliberately injected through a checkpoint-aware
callback receiving the database path, first sequence, and predecessor hash.
Existing genesis-only store verifiers do not satisfy this contract. Until both
journal stores implement that callback and checkpoint-aware append semantics,
this module is an archive-envelope verifier only: it does not authorize rotation,
checkpoint advancement, restore publication, or retention deletion.


## Checkpoint metadata implementation boundary

The Evidence and DecisionTrace stores now support additive checkpoint metadata
(`checkpoint_schema_version`, `checkpoint_sequence`, and
`checkpoint_sha256`) without changing the existing record-hash schema or
rewriting legacy rows. `initialize_checkpoint_metadata()` verifies the current
genesis-anchored journal before installing the initial zero/genesis checkpoint.
It does not advance a checkpoint or remove records.

A non-genesis checkpoint is not self-authenticating merely because it is stored
inside the SQLite database. Active-store `verify()`, `append()`, and backup
verification therefore require the caller to supply the exact
`trusted_checkpoint=(sequence, sha256)` obtained from independent archive-chain
verification. Missing or mismatched trust fails closed. A future rotation
coordinator must only provide that value after verifying the complete archive
chain and must atomically publish the checkpoint with the corresponding active
journal transition.

This increment enables checkpoint-aware append mechanics; it does not authorize
rotation, checkpoint advancement, archive restore publication, or retention.


## Implementation increment: full-chain restore

The generic `restore_archive_chain` path now reconstructs a genesis-to-head
journal from independently verified immutable segments, checks persisted
checkpoint metadata against each manifest boundary, verifies the reconstructed
journal from genesis, re-verifies the source chain, and publishes to a fresh
destination using atomic no-overwrite publication. PR #60 adds a real
EvidenceStore multi-segment restore test. The next increment validates the same
contract for DecisionTrace and replays every restored trace against its paired
Evidence journal.

This remains restore-only. It does not implement rotation, active checkpoint
advancement, signatures, redundant archive copies, or retention. Crash/fault
injection for restore publication and paired Evidence/DecisionTrace recovery
remain required before the archive lifecycle can be considered operational.
