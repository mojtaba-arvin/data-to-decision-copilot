# Spec 0005: bounded read-only SQL execution

## Goal
Execute one validated analytical SELECT against DuckDB with database-enforced read-only access and explicit runtime/output ceilings.

## Security policy
- Only one SELECT/CTE/set-operation statement is accepted.
- SQL functions use a conservative allowlist; unknown functions fail closed.
- External readers, file/network functions, extension loading, COPY, ATTACH, PRAGMA, DDL/DML, and multiple statements are rejected.
- Persistent database files are opened with DuckDB's read-only flag.
- Every execution uses a fresh connection with external access and extension installation/loading disabled.
- The engine is configured with one thread, a memory limit, and no temporary spill allowance.
- Parameters are bound separately; callers must not interpolate values into SQL.

## Runtime and output policy
- A deadline is enforced by asyncio; timeout/cancellation calls DuckDB's interrupt on the connection owned by that request.
- The worker is awaited after interruption and its connection is always closed before the execution slot is released.
- Results are fetched one row at a time and bounded by max_rows and max_bytes.
- captured_rows is the number of rows returned, not the total matching row count.
- truncated means an additional row existed or the next row exceeded the byte ceiling. No total count is inferred.

## Limitations
- DuckDB's memory limit is not an operating-system process memory sandbox.
- Native-code hangs cannot be killed safely inside the Python process; interruption depends on DuckDB returning control.
- File-backed DuckDB access is read-only, but callers remain responsible for protecting database files and directories from concurrent mutation.
- This phase does not yet generate immutable evidence records or provide SQL execution for FileDataSource's in-memory views.
