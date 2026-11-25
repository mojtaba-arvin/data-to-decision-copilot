"""Supervised process-level DuckDB query execution.

The worker is resource-limited and killed as a process group on timeout. This is
not a complete sandbox: deployments handling hostile inputs must additionally
deny network egress and confine filesystem access with OS/container controls.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from d2d.core.sql_executor import (
    ExecutionLimits,
    QueryExecutionError,
    QueryResult,
    QueryTimeoutError,
    QueryWorkerError,
)


@dataclass(frozen=True, slots=True)
class WorkerLimits:
    """Parent/worker limits; memory_bytes is a POSIX address-space ceiling."""

    process_memory_bytes: int = 2_147_483_648
    max_request_bytes: int = 1_048_576
    startup_grace_seconds: float = 2.0

    def __post_init__(self) -> None:
        if self.process_memory_bytes < 256 * 1024 * 1024:
            raise ValueError("process_memory_bytes must be at least 256 MiB")
        if not 256 <= self.max_request_bytes <= 1_048_576:
            raise ValueError("max_request_bytes must be between 256 bytes and 1 MiB")
        if self.startup_grace_seconds < 0:
            raise ValueError("startup_grace_seconds must not be negative")


class IsolatedDuckDBQueryExecutor:
    """Run each query in a fresh supervised process with bounded IPC.

    The catalog is opened read-only by DuckDB. This class does not claim network
    or filesystem sandboxing; use a container/OS policy for untrusted catalogs.
    """

    def __init__(
        self,
        database: str | Path,
        *,
        limits: ExecutionLimits | None = None,
        worker_limits: WorkerLimits | None = None,
    ) -> None:
        if os.name != "posix":
            raise RuntimeError("isolated DuckDB worker currently requires POSIX")
        self._database = str(database)
        self._limits = limits or ExecutionLimits()
        self._worker_limits = worker_limits or WorkerLimits()

    async def execute(
        self,
        sql: str,
        parameters: list[object] | tuple[object, ...] | None = None,
        *,
        dialect: str = "duckdb",
    ) -> QueryResult:
        request: dict[str, Any] = {
            "database": self._database,
            "sql": sql,
            "parameters": list(parameters or ()),
            "dialect": dialect,
            "timeout_seconds": self._limits.timeout_seconds,
            "max_rows": self._limits.max_rows,
            "max_bytes": self._limits.max_bytes,
            "duckdb_memory_limit": self._limits.memory_limit,
            "process_memory_bytes": self._worker_limits.process_memory_bytes,
        }
        payload = json.dumps(
            request, ensure_ascii=False, separators=(",", ":"), default=str
        ).encode("utf-8")
        if len(payload) > self._worker_limits.max_request_bytes:
            raise QueryExecutionError("isolated worker request exceeds configured IPC limit")

        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "d2d.core.isolated_worker",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        timeout = (
            self._limits.timeout_seconds
            + self._worker_limits.startup_grace_seconds
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(payload), timeout=timeout
            )
        except TimeoutError as exc:
            await self._terminate(process)
            raise QueryTimeoutError(
                f"isolated DuckDB worker exceeded {timeout:g}s wall-clock limit"
            ) from exc
        except asyncio.CancelledError:
            await self._terminate(process)
            raise

        if len(stdout) > self._limits.max_bytes + 65_536:
            raise QueryExecutionError("isolated worker response exceeded IPC limit")
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace")[:2048]
            raise QueryWorkerError(
                f"isolated DuckDB worker exited with status {process.returncode}: {detail}"
            )
        try:
            parsed: Any = json.loads(stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            detail = stderr.decode("utf-8", errors="replace")[:512]
            raise QueryWorkerError(
                f"isolated DuckDB worker returned malformed IPC: {detail}"
            ) from exc
        if not isinstance(parsed, dict):
            raise QueryWorkerError("WorkerProtocolError: response must be an object")
        response = cast(dict[str, Any], parsed)
        if not response.get("ok"):
            error_type = str(response.get("error_type", "WorkerError"))
            message = str(response.get("error", "worker failed"))
            raise QueryWorkerError(f"{error_type}: {message}")

        try:
            columns_raw = response["columns"]
            rows_raw = response["rows"]
            if not isinstance(columns_raw, list) or not isinstance(rows_raw, list):
                raise TypeError("columns and rows must be arrays")
            columns = tuple(str(value) for value in cast(list[Any], columns_raw))
            rows = tuple(tuple(row) for row in cast(list[list[Any]], rows_raw))
            captured_rows = int(response["captured_rows"])
            output_bytes = int(response["output_bytes"])
            truncated = bool(response["truncated"])
        except (KeyError, TypeError, ValueError) as exc:
            raise QueryWorkerError("isolated worker response schema is invalid") from exc
        if (
            captured_rows != len(rows)
            or captured_rows > self._limits.max_rows
            or output_bytes > self._limits.max_bytes
            or output_bytes < 0
        ):
            raise QueryWorkerError("isolated worker violated output limits")
        return QueryResult(
            columns=columns,
            rows=rows,
            captured_rows=captured_rows,
            output_bytes=output_bytes,
            truncated=truncated,
        )

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.communicate()
