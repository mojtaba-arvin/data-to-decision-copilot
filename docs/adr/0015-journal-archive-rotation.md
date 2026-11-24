# ADR 0015: immutable segmented archives for append-only journals

## Status

Proposed; implementation is blocked on segment-aware chain continuity and crash-recovery tests.

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
