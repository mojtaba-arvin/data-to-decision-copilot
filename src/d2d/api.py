"""Loopback-only JSON REST API for the shared copilot service."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import secrets
import socket
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import BoundedSemaphore
from typing import Any, cast
from urllib.parse import unquote

from d2d.application import CopilotService

_MAX_BODY_BYTES = 8_192


def create_server(
    service: CopilotService, *, host: str = "127.0.0.1", port: int = 8765,
    max_concurrent_queries: int = 4,
    request_socket_timeout_seconds: float = 10.0,
    bearer_token: str | None = None,
    tls_certfile: str | None = None,
    tls_keyfile: str | None = None,
) -> ThreadingHTTPServer:
    """Create a loopback server or a TLS-protected bearer-authenticated remote server."""
    if not 1 <= max_concurrent_queries <= 32:
        raise ValueError("max_concurrent_queries must be between 1 and 32")
    if not 1.0 <= request_socket_timeout_seconds <= 120.0:
        raise ValueError("request_socket_timeout_seconds must be between 1 and 120")
    try:
        is_loopback = host.lower() == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError as exc:
        raise ValueError("REST API host must be localhost or a valid IP address") from exc
    if bearer_token is not None and len(bearer_token) < 32:
        raise ValueError("REST bearer token must contain at least 32 characters")
    if not is_loopback and (not bearer_token or not tls_certfile or not tls_keyfile):
        raise ValueError(
            "remote REST binds require a bearer token (32+ characters) and TLS certificate/key"
        )
    if (tls_certfile is None) != (tls_keyfile is None):
        raise ValueError("TLS certificate and key must be configured together")

    query_slots = BoundedSemaphore(max_concurrent_queries)

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: dict[str, object]) -> None:
            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if status == 401:
                self.send_header("WWW-Authenticate", 'Bearer realm="d2d"')
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            if bearer_token is None:
                return True
            supplied = self.headers.get("Authorization", "")
            expected = f"Bearer {bearer_token}"
            if secrets.compare_digest(supplied, expected):
                return True
            self._send(401, {"error": "unauthorized"})
            return False

        def do_GET(self) -> None:
            if not self._authorized():
                return
            if self.path == "/health":
                self._send(200, {"status": "ok"})
            elif self.path == "/ready":
                report = service.readiness()
                status = 200 if report["status"] == "ready" else 503
                self._send(status, report)
            elif self.path == "/v1/schema":
                self._send(200, service.schema())
            elif self.path == "/v1/history":
                self._send(200, {"items": service.history()})
            elif self.path == "/v1/traces":
                try:
                    self._send(200, {"items": service.traces()})
                except ValueError:
                    self._send(503, {"error": "trace_verification_failed"})
            elif self.path.startswith("/v1/traces/"):
                trace_id = unquote(self.path.removeprefix("/v1/traces/"))
                if not trace_id or "/" in trace_id:
                    self._send(400, {"error": "invalid_trace_id"})
                    return
                try:
                    trace = service.trace(trace_id)
                except ValueError:
                    self._send(503, {"error": "trace_verification_failed"})
                    return
                if trace is None:
                    self._send(404, {"error": "trace_not_found"})
                else:
                    self._send(200, trace)
            else:
                self._send(404, {"error": "not_found"})

        def do_POST(self) -> None:
            if not self._authorized():
                return
            if self.path not in ("/v1/query", "/v1/decision"):
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
                decoded: object = json.loads(self.rfile.read(length))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send(400, {"error": "invalid_json"})
                return
            if not isinstance(decoded, dict):
                self._send(400, {"error": "request_body_must_be_an_object"})
                return
            payload = cast(dict[str, object], decoded)
            question_value = payload.get("question")
            if not isinstance(question_value, str):
                self._send(400, {"error": "question_must_be_a_string"})
                return
            question = question_value
            if not question.strip() or len(question.encode("utf-8")) > _MAX_BODY_BYTES:
                self._send(400, {"error": "question_empty_or_too_large"})
                return
            if not query_slots.acquire(blocking=False):
                self._send(503, {"error": "server_busy"})
                return
            try:
                try:
                    if self.path == "/v1/query":
                        self._send(200, asyncio.run(service.query(question)))
                    else:
                        evidence_id = payload.get("evidence_id")
                        decision = payload.get("decision")
                        claims = payload.get("claims")
                        if (
                            not isinstance(evidence_id, str)
                            or not isinstance(decision, dict)
                            or not isinstance(claims, list)
                            or not all(isinstance(item, dict) for item in cast(list[object], claims))
                        ):
                            self._send(400, {"error": "invalid_decision_proposal_shape"})
                            return
                        self._send(201, service.decide(
                            question=question,
                            evidence_id=evidence_id,
                            decision=cast(dict[str, object], decision),
                            claims=cast(list[dict[str, object]], claims),
                        ))
                except ValueError:
                    self._send(422, {"error": "request_rejected_by_validation_or_policy"})
                except Exception:
                    self._send(500, {"error": "request_failed"})
            finally:
                query_slots.release()

        def log_message(self, format: str, *args: object) -> None:
            return

    handler: type[BaseHTTPRequestHandler] = Handler

    class BoundedRequestThreadingHTTPServer(ThreadingHTTPServer):
        def get_request(self) -> tuple[socket.socket, Any]:
            connection, address = super().get_request()
            # This bounds idle socket reads/writes, not total query execution time.
            connection.settimeout(request_socket_timeout_seconds)
            return connection, address

    server = BoundedRequestThreadingHTTPServer((host, port), handler)
    if tls_certfile is not None and tls_keyfile is not None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(certfile=tls_certfile, keyfile=tls_keyfile)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    return server
