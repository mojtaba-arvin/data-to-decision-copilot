from __future__ import annotations

import errno
import json
import sqlite3
from typing import cast

import pytest

from d2d.application import CopilotService
from d2d.cli import main
from d2d.core.sql_executor import QueryExecutionError, QueryTimeoutError, QueryWorkerError


class _FailingService:
    def __init__(self, failure: Exception) -> None:
        self.failure = failure
        self.closed = False

    async def query(self, question: str) -> dict[str, object]:
        raise self.failure

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    ("failure", "expected_code", "expected_exit"),
    [
        (ValueError("private validation detail"), "request_rejected_by_validation_or_policy", 2),
        (QueryTimeoutError("private timeout detail"), "query_timeout", 1),
        (QueryWorkerError("private worker detail"), "query_worker_unavailable", 1),
        (QueryExecutionError("private execution detail"), "query_execution_failed", 1),
        (sqlite3.OperationalError("database is locked"), "storage_busy", 1),
        (sqlite3.OperationalError("database or disk is full"), "storage_unavailable", 1),
        (sqlite3.DatabaseError("private corruption detail"), "storage_unavailable", 1),
        (OSError(errno.ENOSPC, "private disk path"), "storage_unavailable", 1),
        (OSError(errno.EACCES, "private storage path"), "storage_unavailable", 1),
        (OSError(errno.EROFS, "private storage path"), "storage_unavailable", 1),
        (RuntimeError("private unexpected detail"), "request_failed", 1),
    ],
)
def test_cli_query_failures_are_machine_readable_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: Exception,
    expected_code: str,
    expected_exit: int,
) -> None:
    import d2d.cli

    service = _FailingService(failure)
    def fake_build_service(**kwargs: object) -> CopilotService:
        return cast(CopilotService, service)

    monkeypatch.setattr(d2d.cli, "build_service", fake_build_service)

    exit_code = main(["query", "--catalog", "catalog.duckdb", "--question", "count rows"])

    output = capsys.readouterr()
    assert exit_code == expected_exit
    assert json.loads(output.out) == {"error": expected_code}
    assert str(failure) not in output.out
    assert output.err == ""
    assert service.closed is True
