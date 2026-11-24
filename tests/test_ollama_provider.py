from __future__ import annotations

import json
from collections.abc import Callable
from io import BytesIO
from urllib.error import URLError
from urllib.request import Request

import pytest

from d2d.core.model_planning import StructuredGenerationRequest
from d2d.core.ollama_provider import (
    OllamaProviderError,
    OllamaStructuredPlanProvider,
)


def generation_request(max_output_bytes: int = 4_096) -> StructuredGenerationRequest:
    return StructuredGenerationRequest(
        system_prompt="Return JSON only.",
        user_prompt="Count rows in orders.",
        response_schema={"type": "object", "properties": {"limit": {"type": "integer"}}},
        max_output_bytes=max_output_bytes,
    )


def response_stub(content: bytes) -> Callable[[Request, float], FakeResponse]:
    def fake_urlopen(request: Request, timeout: float) -> FakeResponse:
        return FakeResponse(content)

    return fake_urlopen


class FakeResponse:
    def __init__(self, content: bytes) -> None:
        self.content = BytesIO(content)

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self.content.read(size)


def test_ollama_sends_json_schema_request_to_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request: Request, timeout: float) -> FakeResponse:
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse(json.dumps({"response": '{"limit": 10}', "done": True}).encode())

    monkeypatch.setattr("d2d.core.ollama_provider.urlopen", fake_urlopen)
    provider = OllamaStructuredPlanProvider(model="qwen3:8b")
    result = provider.generate(generation_request())

    request = captured["request"]
    assert isinstance(request, Request)
    assert isinstance(request.data, bytes)
    payload = json.loads(request.data)
    assert request.full_url == "http://127.0.0.1:11434/api/generate"
    assert payload["model"] == "qwen3:8b"
    assert payload["stream"] is False
    assert payload["think"] is False
    assert payload["format"] == generation_request().response_schema
    assert payload["options"]["temperature"] == 0
    assert captured["timeout"] == 30.0
    assert result == '{"limit": 10}'


@pytest.mark.parametrize(
    "base_url",
    [
        "https://127.0.0.1:11434",
        "http://example.com:11434",
        "http://127.0.0.1.evil.test:11434",
        "http://user:pass@127.0.0.1:11434",
        "http://127.0.0.1:11434/proxy",
        "http://127.0.0.1:11434?target=example.com",
    ],
)
def test_rejects_non_loopback_or_overridden_urls(base_url: str) -> None:
    with pytest.raises(ValueError, match="loopback"):
        OllamaStructuredPlanProvider(model="qwen3:8b", base_url=base_url)


@pytest.mark.parametrize("timeout", [0, 0.09, 120.1])
def test_rejects_unbounded_timeout(timeout: float) -> None:
    with pytest.raises(ValueError, match="timeout"):
        OllamaStructuredPlanProvider(model="qwen3:8b", timeout_seconds=timeout)


def test_wraps_connection_failure_and_does_not_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def fail(request: Request, timeout: float) -> None:
        nonlocal calls
        calls += 1
        raise URLError("offline")

    monkeypatch.setattr("d2d.core.ollama_provider.urlopen", fail)
    provider = OllamaStructuredPlanProvider(model="qwen3:8b")
    with pytest.raises(OllamaProviderError, match="failed or timed out"):
        provider.generate(generation_request())
    assert calls == 1


def test_rejects_invalid_envelope_and_incomplete_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OllamaStructuredPlanProvider(model="qwen3:8b")
    monkeypatch.setattr(
        "d2d.core.ollama_provider.urlopen",
        response_stub(b"not-json"),
    )
    with pytest.raises(OllamaProviderError, match="invalid JSON"):
        provider.generate(generation_request())

    monkeypatch.setattr(
        "d2d.core.ollama_provider.urlopen",
        response_stub(b'{"response":"{}","done":false}'),
    )
    with pytest.raises(OllamaProviderError, match="did not complete"):
        provider.generate(generation_request())


def test_rejects_oversized_response_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OllamaStructuredPlanProvider(model="qwen3:8b")
    monkeypatch.setattr(
        "d2d.core.ollama_provider.urlopen",
        response_stub(b" " * 131_073),
    )
    with pytest.raises(OllamaProviderError, match="envelope"):
        provider.generate(generation_request())


def test_rejects_generated_text_over_request_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OllamaStructuredPlanProvider(model="qwen3:8b")
    monkeypatch.setattr(
        "d2d.core.ollama_provider.urlopen",
        response_stub(json.dumps({"response": "x" * 5, "done": True}).encode()),
    )
    with pytest.raises(OllamaProviderError, match="output"):
        provider.generate(generation_request(max_output_bytes=4))


def test_accepts_localhost_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "d2d.core.ollama_provider.urlopen",
        response_stub(json.dumps({"response": "{}", "done": True}).encode()),
    )
    provider = OllamaStructuredPlanProvider(
        model="qwen3:8b", base_url="http://localhost:11434/"
    )
    assert provider.generate(generation_request()) == "{}"
