"""Local Ollama adapter for strict structured QueryPlan generation.

The adapter intentionally permits only plain HTTP to a loopback address. It
never forwards schema metadata to a remote host and never executes model output.
"""
from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from d2d.core.model_planning import StructuredGenerationRequest, StructuredPlanProvider

_MAX_ENVELOPE_BYTES = 131_072


class OllamaProviderError(RuntimeError):
    """Raised when the local Ollama service cannot return a bounded response."""


@dataclass(frozen=True, slots=True)
class OllamaStructuredPlanProvider(StructuredPlanProvider):
    """Call a loopback Ollama /api/generate endpoint with JSON-schema output.

    No retries are performed: the caller controls request lifecycle, and a
    failed generation must not silently trigger duplicate inference.
    """

    model: str
    base_url: str = "http://127.0.0.1:11434"
    timeout_seconds: float = 30.0
    max_output_tokens: int = 2_048

    def __post_init__(self) -> None:
        if not self.model.strip() or len(self.model) > 256:
            raise ValueError("model must contain between 1 and 256 characters")
        if not 0.1 <= self.timeout_seconds <= 120:
            raise ValueError("timeout_seconds must be between 0.1 and 120")
        if not 1 <= self.max_output_tokens <= 8_192:
            raise ValueError("max_output_tokens must be between 1 and 8192")
        _validate_loopback_url(self.base_url)

    def is_ready(self) -> bool:
        """Check the loopback API and configured model tag with a one-second bound."""
        request = Request(
            f"{self.base_url.rstrip('/')}/api/tags",
            headers={"Accept": "application/json"},
            method="GET",
        )
        try:
            with urlopen(request, timeout=1.0) as response:
                envelope = response.read(_MAX_ENVELOPE_BYTES + 1)
        except (HTTPError, URLError, TimeoutError, OSError):
            return False
        if len(envelope) > _MAX_ENVELOPE_BYTES:
            return False
        try:
            parsed: object = json.loads(envelope)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False
        if not isinstance(parsed, dict):
            return False
        models = parsed.get("models")
        if not isinstance(models, list):
            return False
        return any(
            isinstance(model, dict) and model.get("name") == self.model
            for model in models
        )

    def generate(self, request: StructuredGenerationRequest) -> str:
        """Generate one JSON response, with strict local-network and size bounds."""
        payload = {
            "model": self.model,
            "system": request.system_prompt,
            "prompt": request.user_prompt,
            "format": request.response_schema,
            "stream": False,
            "options": {
                "temperature": 0,
                "num_predict": min(self.max_output_tokens, request.max_output_bytes),
            },
        }
        # Qwen3 exposes a separate thinking channel by default. Disable it for
        # strict structured-output requests so the response field contains JSON.
        if self.model.casefold().startswith("qwen3"):
            payload["think"] = False
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        http_request = Request(
            f"{self.base_url.rstrip('/')}/api/generate",
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urlopen(http_request, timeout=self.timeout_seconds) as response:
                envelope = response.read(_MAX_ENVELOPE_BYTES + 1)
        except HTTPError as exc:
            detail = exc.read(512).decode("utf-8", errors="replace")
            raise OllamaProviderError(
                f"Ollama returned HTTP {exc.code}: {detail}"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise OllamaProviderError("Ollama request failed or timed out") from exc

        if len(envelope) > _MAX_ENVELOPE_BYTES:
            raise OllamaProviderError("Ollama response envelope exceeds the byte limit")
        try:
            parsed: object = json.loads(envelope)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OllamaProviderError("Ollama returned an invalid JSON envelope") from exc
        if not isinstance(parsed, dict):
            raise OllamaProviderError("Ollama response envelope must be a JSON object")
        decoded = cast(dict[str, object], parsed)
        result = decoded.get("response")
        if not isinstance(result, str):
            raise OllamaProviderError("Ollama response envelope has no string response field")
        if len(result.encode("utf-8")) > request.max_output_bytes:
            raise OllamaProviderError("Ollama generated output exceeds the requested byte limit")
        if decoded.get("done") is not True:
            raise OllamaProviderError("Ollama did not complete structured generation")
        return result


def _validate_loopback_url(value: str) -> None:
    """Reject remote hosts, credentials, non-HTTP schemes, and URL overrides."""
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        if (
            parsed.scheme != "http"
            or host is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError
        if host.lower() == "localhost":
            return
        if not ipaddress.ip_address(host).is_loopback:
            raise ValueError
    except ValueError as exc:
        raise ValueError("Ollama base_url must be plain HTTP on a loopback host") from exc
