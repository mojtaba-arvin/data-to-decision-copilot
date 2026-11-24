"""Private subprocess entry point for bounded DuckDB query execution.

This is process separation and resource limiting, not a complete OS sandbox:
filesystem and network confinement must be supplied by the deployment platform.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any


def _apply_process_limits(request: dict[str, Any]) -> None:
    if os.name != "posix":
        raise RuntimeError("isolated DuckDB worker requires POSIX resource limits")
    import resource

    timeout = float(request["timeout_seconds"])
    memory_bytes = int(request["process_memory_bytes"])
    cpu_seconds = max(1, int(timeout) + 1)
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(1_048_577)
        if len(raw) > 1_048_576:
            raise ValueError("worker request exceeds 1 MiB")
        request = json.loads(raw)
        if not isinstance(request, dict):
            raise ValueError("worker request must be an object")
        _apply_process_limits(request)

        # Import native/query dependencies only after applying OS resource limits.
        import asyncio

        from d2d.core.sql_executor import DuckDBQueryExecutor, ExecutionLimits

        limits = ExecutionLimits(
            timeout_seconds=float(request["timeout_seconds"]),
            max_rows=int(request["max_rows"]),
            max_bytes=int(request["max_bytes"]),
            memory_limit=str(request["duckdb_memory_limit"]),
        )
        executor = DuckDBQueryExecutor(str(request["database"]), limits=limits)
        result = asyncio.run(
            executor.execute(
                str(request["sql"]),
                request.get("parameters"),
                dialect=str(request.get("dialect", "duckdb")),
            )
        )
        response = {
            "ok": True,
            "columns": result.columns,
            "rows": result.rows,
            "captured_rows": result.captured_rows,
            "output_bytes": result.output_bytes,
            "truncated": result.truncated,
        }
    except Exception as exc:
        response = {
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc)[:2048],
        }
    encoded = json.dumps(
        response, ensure_ascii=False, separators=(",", ":"), default=str
    ).encode("utf-8")
    if len(encoded) > 1_100_000:
        encoded = b'{"ok":false,"error_type":"WorkerProtocolError","error":"response too large"}'
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
