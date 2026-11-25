# ADR 0015: immutable segmented archives for append-only journals

## Status

Partially implemented; segment verification and full-chain restore are available. Rotation remains blocked on crash-recovery and idempotency tests.

## Context

Evidence and DecisionTrace are separate append-only SQLite journals with per-record SHA-256 digests and a previous-entry hash chain. Online backups provide consistent snapshots and verification, but they do not compact the active database. Deleting rows or resetting a chain would invalidate audit history.

## Decision

Use immutable, independently checksummed archive segments with canonical manifests and cross-segment chain continuity. Rotate only after a verified SQLite online backup is published durably. Advance the active-journal checkpoint only after archive publication succeeds. Keep signing optional at the adapter level but make unsigned status explicit; production/high-assurance deployments should anchor or sign manifest digests with keys stored outside the archive.

Retention is an archive lifecycle policy. The default is no deletion. A complete segment can be deleted only after policy approval, verified redundant archival, and an auditable deletion event.

## Alternatives rejected

- **DELETE old rows in place:** breaks append-only semantics and sequence/hash-chain verification.
- **VACUUM as retention:** changes physical storage but does not define an auditable archive or retention protocol.
- **Copy the SQLite file while active:** may omit WAL state or capture an inconsistent snapshot.
- **Reset the journal to genesis after each backup:** creates an unanchored chain discontinuity.
- **Trust a manifest stored only beside its database:** an attacker who can rewrite both can replace both checksums.

## Consequences

- A segment-aware verifier and checkpoint-aware schema migration are prerequisites.
- Rotation is a multi-phase protocol requiring crash recovery and idempotency tests.
- Archive verification can work offline; public keys/trust-store changes are operational inputs.
- Until implementation is complete, operators must keep verified backups and monitor disk growth; no automatic pruning is supported.


## Implementation status (2026-11-25)

Segment-aware archive verification, checkpoint-aware journal segments, durable
manifest publication, and generic full-chain restore have been merged. Full-chain
restore is verified for both Evidence and DecisionTrace; paired historical
replay against the restored EvidenceStore passed (PRs #61 and #63). This does not authorize live
journal rotation or retention. Rotation remains disabled until crash injection
covers every durable transition, concurrent append/WAL behavior is proven, and
recovery is idempotent. Retention remains disabled until redundant archive-copy
verification and auditable deletion policy are implemented.


PR #64 adds `d2d journal backup` and `d2d journal restore`. These commands
publish the paired Evidence/DecisionTrace directories atomically and fail
closed on overwrite or unsupported no-replace directory publication. They do
not rotate active journals or advance checkpoints. The backup covers only the
two journals, not catalogs, query history, configuration, secrets, or model
artifacts. The archive manifests remain unsigned.


## Paired-snapshot consistency test boundary

The paired backup takes independent SQLite online snapshots, then verifies every
DecisionTrace evidence reference against the Evidence snapshot before publishing
the pair. A regression test deliberately appends a new Evidence record and
DecisionTrace after the Evidence snapshot but before the trace snapshot; the
backup must fail closed and leave the destination unpublished. This validates
cross-snapshot referential consistency, not a globally atomic point-in-time
snapshot across two independent SQLite databases. Operators must quiesce writes
when they require a single business-time cut across both journals.


## Fault-injection progress (PRs #67–#69)

The paired operator backup now has regression coverage for selected failure windows:

- If a DecisionTrace added after the Evidence snapshot references an Evidence row absent from that snapshot, paired backup fails closed and publishes no pair (PR #67).
- If final artifact fsync reports ENOSPC, or atomic no-replace directory publication fails, no destination or staging directory is left behind and both live journals remain verifiable (PR #68).
- A paired online backup captures committed Evidence and DecisionTrace pages in WAL mode while independent read transactions remain open; the restored pair verifies and historical trace replay succeeds (PR #69).

These tests do not establish a globally atomic point-in-time snapshot across two independent SQLite databases. Operators requiring one business-time cut must quiesce writes. They also do not complete process/power-loss injection at every durability boundary, concurrent-writer stress during backup/rotation, lock-contention testing, checkpoint transition recovery, or idempotent rotation. Rotation and retention remain disabled.
