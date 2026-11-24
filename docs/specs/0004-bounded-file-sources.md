# Spec 0004: bounded CSV and Parquet file sources

## Goal
Add explicit CSV and Parquet metadata adapters without introducing arbitrary file access or a general SQL execution surface.

## Policy
- Callers provide an explicit list of relative file paths; glob expansion is not supported.
- The configured root must be a directory and must not itself be a symlink.
- Absolute paths, empty paths, traversal components, symlink components, missing files, and non-regular files are rejected.
- Only CSV and Parquet extensions are accepted.
- Each file is stat-checked against a configurable byte ceiling before DuckDB opens it.
- CSV inference uses an explicit positive sample-size bound.
- File count and discovered columns obey the shared DiscoveryLimits; violations fail closed.
- DuckDB runs in an isolated in-memory connection. The adapter exposes metadata methods only.

## Acceptance criteria
- [x] Explicit CSV and Parquet files can be discovered.
- [x] Paths cannot escape the configured root or traverse symlinks.
- [x] Unsupported extensions and oversized files are rejected before scanning.
- [x] CSV inference has a configured sample bound.
- [x] Relation and column limits fail closed.
- [x] The adapter satisfies the structural DataSource protocol.

## Limitations
The byte ceiling is checked before open but is not an immutable filesystem lease; callers must prevent untrusted concurrent mutation of the source directory. CSV sample size bounds inference, not the later cost of scanning rows. Parquet metadata parsing can still consume resources proportional to file metadata. This adapter does not execute caller SQL, and does not claim to sandbox future analytical queries.
