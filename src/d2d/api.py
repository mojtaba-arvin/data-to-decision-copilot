"""Loopback-only JSON REST API for the shared copilot service."""
from __future__ import annotations

import asyncio
import ipaddress
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Type

from d2d.application import CopilotService

_MAX_BODY_BYTES = 8_192


def create_server(
    service: CopilotService, *, host: str = "127.0.0.1", port: int = 8765,
) -> ThreadingHTTPServer:
    """Create a local API server; remote binds are rejected because auth is absent."""
    if host != "localhost":
        try:
            if not ipaddress.ip_address(host).is_loopback:
                raise ValueError("REST API may bind only to a loopback address")
        except ValueError as exc:
            raise ValueError("REST API may bind only to a loopback address") from exc

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: dict[str, object]) -> None:
            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/health":
                self._send(200, {"status": "ok"})
            elif self.path == "/v1/schema":
                self._send(200, service.schema())
            elif self.path == "/v1/history":
                self._send(200, {"items": service.history()})
            else:
                self._send(404, {"error": "not_found"})

        def do_POST(self) -> None:
            if self.path != "/v1/query":
                self._send(404, {"error": "not_found"})
                return
            if self.headers.get_content_type() != "application/json":
                self._send(415, {"error": "content_type_must_be_application_json"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send(400, {"error": "invalid_content_length"})
                return
            if length < 1 or length > _MAX_BODY_BYTES:
                self._send(413, {"error": "request_body_out_of_bounds"})
                return
            try:
                payload = json.loads(self.rfile.read(length))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send(400, {"error": "invalid_json"})
                return
            if not isinstance(payload, dict) or not isinstance(payload.get("question"), str):
                self._send(400, {"error": "question_must_be_a_string"})
                return
            question = payload["question"]
            if not question.strip() or len(question.encode("utf-8")) > _MAX_BODY_BYTES:
                self._send(400, {"error": "question_empty_or_too_large"})
                return
            try:
                self._send(200, asyncio.run(service.query(question)))
            except ValueError:
                self._send(422, {"error": "query_rejected"})
            except Exception:
                self._send(500, {"error": "query_failed"})

        def log_message(self, format: str, *args: object) -> None:
            return

    handler: Type[BaseHTTPRequestHandler] = Handler
    return ThreadingHTTPServer((host, port), handler)
