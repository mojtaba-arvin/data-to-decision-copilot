from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import NoReturn
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from d2d.api import create_server
from d2d.application import CopilotService, build_service
from d2d.connectors.base import SchemaSnapshot
from d2d.core.model_planning import (
    ModelBackedQueryPlanner,
    QueryAbstainedError,
    StructuredGenerationRequest,
)
from d2d.core.ollama_provider import OllamaProviderError, OllamaStructuredPlanProvider
from d2d.demo import create_demo_catalog


def _request(server: ThreadingHTTPServer, question: object) -> tuple[int, dict[str, object]]:
    host, port = server.server_address[:2]
    request = Request(
        f"http://{host}:{port}/v1/query",
        data=json.dumps({"question": question}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except HTTPError as error:
        return error.code, json.loads(error.read())


def _serve(service: CopilotService) -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = create_server(service, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _service(tmp_path: Path, *, provider: str = "rules") -> CopilotService:
    catalog = tmp_path / "catalog.duckdb"
    create_demo_catalog(catalog)
    return build_service(
        catalog_path=str(catalog),
        evidence_path=str(tmp_path / "evidence.sqlite"),
        trace_path=str(tmp_path / "traces.sqlite"),
        history_path=str(tmp_path / "history.sqlite"),
        provider=provider,
    )


def test_rest_reports_invalid_input_with_stable_code(tmp_path: Path) -> None:
    service = _service(tmp_path)
    server, thread = _serve(service)
    try:
        status, payload = _request(server, "   ")
        assert status == 400
        assert payload == {"error": "invalid_input"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        service.close()


def test_rest_reports_explicit_model_abstention(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def abstain(
        self: ModelBackedQueryPlanner, question: str, snapshot: SchemaSnapshot
    ) -> NoReturn:
        raise QueryAbstainedError("private model reasoning")

    monkeypatch.setattr(ModelBackedQueryPlanner, "plan", abstain)
    service = _service(tmp_path, provider="ollama")
    server, thread = _serve(service)
    try:
        status, payload = _request(server, "show an unsupported metric")
        assert status == 422
        assert payload == {"error": "query_abstained"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        service.close()


def test_rest_reports_unavailable_model_without_leaking_provider_detail(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    def unavailable(
        self: OllamaStructuredPlanProvider, request: StructuredGenerationRequest
    ) -> str:
        raise OllamaProviderError("private endpoint and model details")

    monkeypatch.setattr(OllamaStructuredPlanProvider, "generate", unavailable)
    service = _service(tmp_path, provider="ollama")
    server, thread = _serve(service)
    try:
        status, payload = _request(server, "count rows in orders")
        assert status == 503
        assert payload == {"error": "model_unavailable"}
        assert "private endpoint and model details" not in caplog.text
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        service.close()
