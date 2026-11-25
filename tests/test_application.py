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



def test_readiness_includes_the_configured_ollama_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = tmp_path / "analytics.duckdb"
    connection = duckdb.connect(str(catalog))
    connection.execute("CREATE TABLE orders (id INTEGER)")
    connection.close()

    monkeypatch.setattr(
        "d2d.core.ollama_provider.OllamaStructuredPlanProvider.is_ready",
        lambda self: False,
    )
    service = application.build_service(
        catalog_path=str(catalog),
        evidence_path=str(tmp_path / "evidence.sqlite"),
        history_path=str(tmp_path / "history.sqlite"),
        trace_path=str(tmp_path / "traces.sqlite"),
        provider="ollama",
        model="qwen3:8b",
    )
    try:
        report = service.readiness()
        assert report["status"] == "not_ready"
        assert report["checks"]["model_provider"] == "unavailable"
    finally:
        service.close()
