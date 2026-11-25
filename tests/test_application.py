from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

import d2d.application as application
from d2d.connectors.duckdb import DuckDBConnector
from d2d.query_history import SQLiteQueryHistoryStore


def test_build_service_closes_partial_resources_when_trace_store_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = tmp_path / "analytics.duckdb"
    connection = duckdb.connect(str(catalog))
    connection.execute("CREATE TABLE orders (id INTEGER)")
    connection.close()

    closed: list[str] = []
    original_catalog_close = DuckDBConnector.close
    original_history_close = SQLiteQueryHistoryStore.close

    def track_catalog_close(source: DuckDBConnector) -> None:
        closed.append("catalog")
        original_catalog_close(source)

    def track_history_close(store: SQLiteQueryHistoryStore) -> None:
        closed.append("history")
        original_history_close(store)

    def fail_trace_store(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated trace-store startup failure")

    monkeypatch.setattr(DuckDBConnector, "close", track_catalog_close)
    monkeypatch.setattr(SQLiteQueryHistoryStore, "close", track_history_close)
    monkeypatch.setattr(application, "SQLiteDecisionTraceStore", fail_trace_store)

    with pytest.raises(RuntimeError, match="simulated trace-store startup failure"):
        application.build_service(
            catalog_path=str(catalog),
            evidence_path=str(tmp_path / "evidence.sqlite"),
            history_path=str(tmp_path / "history.sqlite"),
            trace_path=str(tmp_path / "traces.sqlite"),
        )

    assert closed == ["history", "catalog"]



def test_invalid_history_retention_fails_before_opening_persistent_stores(tmp_path: Path) -> None:
    catalog = tmp_path / "analytics.duckdb"
    connection = duckdb.connect(str(catalog))
    connection.execute("CREATE TABLE orders (id INTEGER)")
    connection.close()
    evidence_path = tmp_path / "evidence.sqlite"
    history_path = tmp_path / "history.sqlite"

    with pytest.raises(ValueError, match="history_max_records"):
        application.build_service(
            catalog_path=str(catalog),
            evidence_path=str(evidence_path),
            history_path=str(history_path),
            trace_path=str(tmp_path / "traces.sqlite"),
            history_max_records=0,
        )

    assert not evidence_path.exists()
    assert not history_path.exists()
    assert not (tmp_path / "traces.sqlite").exists()
