# ADR 0006: construct evidence only from executed query results

## Status
Accepted for Phase 5.

## Decision
Use a server-side factory that accepts validated SQL, a bounded executor result, and trusted source/version metadata. It creates a unique ID, UTC timestamp, query digest, and canonical result digest. Evidence models are frozen and preserve ordered row tuples so duplicate SQL column labels are not lost.

## Consequences
- Captured row count is distinct from total matching rows.
- Truncation is first-class metadata.
- Raw parameter values are not stored or hashed.
- The result digest detects changes when recomputed, but is not a digital signature and does not alone provide durable tamper evidence.
- Durable append-only storage, signatures, and evidence verification remain future work.
