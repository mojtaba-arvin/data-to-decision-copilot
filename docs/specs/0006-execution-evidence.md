# Spec 0006: execution-derived immutable evidence

## Goal
Create evidence records exclusively from validated SQL and the bounded executor's actual result. An LLM may reference evidence IDs but cannot author or mutate evidence.

## Record contents
- Unique server-generated evidence ID.
- Canonical validated SQL and dialect, plus SHA-256 query fingerprint.
- Source identifier and caller-supplied schema/data version identifiers.
- UTC execution timestamp and measured execution duration.
- Ordered column names and captured rows as tuples (preserving duplicate SQL column labels).
- Captured row count, output byte accounting, and explicit truncation flag.
- SHA-256 digest of a canonical representation of captured result metadata and values.

## Semantics and privacy
- row_count means captured rows only; it is never the total matching row count.
- truncated indicates the executor observed another row or could not capture the next row within the output budget.
- Raw bind parameter values are neither persisted nor hashed; low-entropy hashes can expose secrets through dictionary attacks.
- Source identifiers and schema fingerprints come from the trusted integration layer, not the model.
- Records are immutable Pydantic models. Durable storage, signatures, and append-only tamper evidence are out of scope.

## Trust boundary
The factory accepts a QueryResult from the executor, revalidates SQL, checks row/column/count consistency, and constructs IDs, timestamps, and hashes server-side.
