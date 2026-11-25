# Paired journal backup and restore

## Backup

Create a consistent, independently verified snapshot pair without rotating or
truncating the active journals:

```bash
d2d journal backup \
  --evidence-db ./d2d-evidence.sqlite \
  --trace-db ./d2d-traces.sqlite \
  --output ./backup-2026-11-25
```

The database arguments default to `d2d-evidence.sqlite` and
`d2d-traces.sqlite`. Both source files must already exist and contain the
supported append-only journal schema. The command uses SQLite's online backup
API, creates one full genesis-to-head segment for each journal, verifies the
archive manifests and database bytes, checks every DecisionTrace against the
backed-up Evidence journal, and atomically publishes the complete backup
directory without overwriting an existing path. Empty genesis journals are
supported. Non-genesis active checkpoints are rejected; rotation and checkpoint
advancement are not part of this command.

The backup includes only the Evidence and DecisionTrace journals. It does not
include the source catalog, query history, application configuration, model
weights, or secrets; those require separate operator backup procedures.

## Restore

The operator restore command reconstructs both append-only journals into one
fresh directory:

```bash
d2d journal restore \
  --evidence-archive ./backup/evidence \
  --trace-archive ./backup/traces \
  --output ./restored-2026-11-25
```

Each archive directory must contain one or more `*.manifest.json` files and
the SQLite database artifact named by each manifest's `database_file` field.
The command orders segments by `first_sequence`, then independently verifies
manifest canonicalization/digests, database bytes, segment checkpoints,
sequence/hash continuity, and manifest links. It rejects symlinked artifacts,
unsafe database paths, missing artifacts, unsupported schema versions, and an
existing destination.

Both journals are reconstructed in a private staging directory. Before
publication, the command verifies the restored Evidence journal and verifies
all DecisionTrace-to-Evidence references against that restored Evidence
journal. It then fsyncs the restored databases and staging directory and
publishes the entire directory with an atomic no-replace rename. If the host
does not support an atomic no-replace directory rename, the command fails
closed; it does not fall back to a non-atomic copy or overwrite.

The restore command does **not** create archives, rotate active journals, advance
checkpoints, or prune data. Rotation and retention remain disabled pending
crash-safe lifecycle transitions, fault-injection coverage, redundant-copy
verification, and an explicit retention policy.

Manifest hash chains provide integrity and tamper detection relative to the
provided chain, but the current format is unsigned. They do not authenticate
who produced an archive or protect against an attacker who can replace every
segment and manifest consistently. Detached signatures and external key
management remain separate acceptance criteria in Issue #37.


## Operator notes

This paired backup is a full genesis-to-head snapshot of the two journals at
their respective SQLite backup snapshots. The command verifies cross-journal
references before publishing; if writes occur between the two snapshots and a
DecisionTrace references Evidence absent from the Evidence snapshot, backup
fails rather than publishing an inconsistent pair. No active journal is
truncated or rotated.


### Publication durability failure

The restore uses a no-replace atomic publication step. Failures before that
step (including staged database fsync failure) leave no published destination;
the command removes its private staging directory and propagates the error.

A failure while fsyncing the destination's parent directory occurs **after**
the verified restore directory has become visible. In that case, the command
reports the fsync error but deliberately does not remove the published
destination: its durability across a host crash is uncertain, and deleting it
could destroy the only visible recovery copy.

If this occurs:

1. Do not immediately retry into the same destination; it already exists.
2. Keep the destination and source archives intact.
3. Have an operator independently verify both restored journals and their
   cross-journal references before using the destination; also inspect
   filesystem/host storage health.
4. Treat the restore as unconfirmed until the directory and its parent have
   been durably synced or a fresh restore to another destination completes.

Never delete the destination merely because the command returned an error
after publication. Retention and automatic cleanup remain disabled.
