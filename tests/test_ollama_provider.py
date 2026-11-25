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



def test_readiness_checks_exact_configured_model_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request: Request, timeout: float) -> FakeResponse:
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse(json.dumps({"models": [{"name": "qwen3:8b"}]}).encode())

    monkeypatch.setattr("d2d.core.ollama_provider.urlopen", fake_urlopen)
    provider = OllamaStructuredPlanProvider(model="qwen3:8b")
    assert provider.is_ready() is True
    request = captured["request"]
    assert isinstance(request, Request)
    assert request.full_url == "http://127.0.0.1:11434/api/tags"
    assert captured["timeout"] == 1.0


def test_readiness_is_false_when_model_missing_or_provider_offline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OllamaStructuredPlanProvider(model="qwen3:8b")
    monkeypatch.setattr(
        "d2d.core.ollama_provider.urlopen",
        response_stub(json.dumps({"models": [{"name": "qwen3:4b"}]}).encode()),
    )
    assert provider.is_ready() is False

    def offline(request: Request, timeout: float) -> FakeResponse:
        raise URLError("offline")

    monkeypatch.setattr("d2d.core.ollama_provider.urlopen", offline)
    assert provider.is_ready() is False
