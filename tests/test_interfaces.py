from __future__ import annotations

import asyncio
import json
import logging
import socket
import threading
from io import StringIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import duckdb
import pytest

from d2d.api import create_server
from d2d.application import build_service
from d2d.mcp import run_stdio


@pytest.fixture
def catalog(tmp_path: Path) -> Path:
    path = tmp_path / "analytics.duckdb"
    connection = duckdb.connect(str(path))
    connection.execute("CREATE TABLE orders (id INTEGER, amount INTEGER)")
    connection.execute("INSERT INTO orders VALUES (1, 10), (2, 20)")
    connection.close()
    return path


def test_shared_service_executes_query_and_records_history(catalog: Path, tmp_path: Path) -> None:
    service = build_service(
        catalog_path=str(catalog), evidence_path=str(tmp_path / "evidence.sqlite")
    )
    try:
        result = asyncio.run(service.query("count rows in orders"))
        assert result.get("rows") == [[2]]
        assert result.get("evidence_id")
        assert "parameters" not in result
        parameter_count = result.get("parameter_count")
        assert isinstance(parameter_count, int)
        assert parameter_count >= 0
        assert service.history()[0]["question"] == "count rows in orders"
        assert '"name": "orders"' in json.dumps(service.schema())
    finally:
        service.close()


def test_shared_service_can_execute_queries_in_isolated_worker(
    catalog: Path, tmp_path: Path
) -> None:
    service = build_service(
        catalog_path=str(catalog),
        evidence_path=str(tmp_path / "isolated-evidence.sqlite"),
        isolated_worker=True,
    )
    try:
        result = asyncio.run(service.query("count rows in orders"))
        assert result["rows"] == [[2]]
        assert result["evidence_id"]
        assert service.history()[0]["evidence_sequence"] == 1
    finally:
        service.close()


def test_rest_api_serves_query_history_and_schema(catalog: Path, tmp_path: Path) -> None:
    service = build_service(
        catalog_path=str(catalog), evidence_path=str(tmp_path / "api-evidence.sqlite"),
        trace_path=str(tmp_path / "api-traces.sqlite"),
    )
    server = create_server(service, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    base = f"http://{host}:{port}"
    try:
        request = Request(
            base + "/v1/query",
            data=json.dumps({"question": "count rows in orders"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            payload = json.loads(response.read())
        assert payload["rows"] == [[2]]
        assert "parameters" not in payload
        assert isinstance(payload["parameter_count"], int)
        with urlopen(base + "/health", timeout=5) as response:
            assert json.loads(response.read())["status"] == "ok"
        with urlopen(base + "/ready", timeout=5) as response:
            readiness = json.loads(response.read())
        assert readiness["status"] == "ready"
        assert readiness["checks"] == {
            "catalog": "ok",
            "evidence_journal": "ok",
            "decision_trace_journal": "ok",
        }
        with urlopen(base + "/v1/history", timeout=5) as response:
            history = json.loads(response.read())
        assert history["items"][0]["evidence_id"] == payload["evidence_id"]
        with urlopen(base + "/v1/schema", timeout=5) as response:
            schema = json.loads(response.read())
        assert schema["tables"][0]["name"] == "orders"
        with urlopen(base + "/v1/traces", timeout=5) as response:
            traces = json.loads(response.read())
        assert traces["items"] == []

        decision_body = {
            "question": "count rows in orders",
            "evidence_id": payload["evidence_id"],
            "decision": {
                "action": "Review order volume before changing inventory.",
                "rationale": "The executed query returned two order rows; this alone does not establish a trend.",
                "evidence_ids": [payload["evidence_id"]],
                "confidence": "medium",
                "alternatives": [{
                    "action": "Keep the current inventory plan.",
                    "trade_off": "Avoids a premature change but may delay a response to demand.",
                }],
                "reversal_conditions": ["Reassess after a comparable time-window query."],
                "caveats": ["Only a row count was queried; no revenue or trend was measured."],
            },
            "claims": [{
                "claim_id": "observed-row-count",
                "statement": "The query returned 2 rows.",
                "evidence_ids": [payload["evidence_id"]],
                "kind": "fact",
            }],
        }
        decision_request = Request(
            base + "/v1/decision", data=json.dumps(decision_body).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urlopen(decision_request, timeout=5) as response:
            decision_result = json.loads(response.read())
            assert response.status == 201
        assert decision_result["proposal_origin"] == "caller_supplied"
        assert decision_result["policy"]["allowed"] is True
        assert decision_result["trace"]["evidence"][0]["evidence_id"] == payload["evidence_id"]
        with urlopen(base + "/v1/traces", timeout=5) as response:
            traces = json.loads(response.read())
        assert len(traces["items"]) == 1
        assert traces["items"][0]["trace_id"] == decision_result["trace"]["trace_id"]

        rejected_body = json.loads(json.dumps(decision_body))
        rejected_body["decision"]["confidence"] = "high"
        rejected_request = Request(
            base + "/v1/decision", data=json.dumps(rejected_body).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with pytest.raises(HTTPError) as rejected:
            urlopen(rejected_request, timeout=5)
        assert rejected.value.code == 422
        mismatched_body = json.loads(json.dumps(decision_body))
        mismatched_body["question"] = "count rows in invoices"
        mismatched_request = Request(
            base + "/v1/decision", data=json.dumps(mismatched_body).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with pytest.raises(HTTPError) as mismatched:
            urlopen(mismatched_request, timeout=5)
        assert mismatched.value.code == 422
        with pytest.raises(HTTPError) as missing_trace:
            urlopen(base + "/v1/traces/not-a-real-trace", timeout=5)
        assert missing_trace.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        service.close()


def test_mcp_stdio_exposes_only_query_and_schema_tools(catalog: Path, tmp_path: Path) -> None:
    service = build_service(
        catalog_path=str(catalog), evidence_path=str(tmp_path / "mcp-evidence.sqlite")
    )
    incoming = "\n".join(
        [
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
            json.dumps({
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "query_analytics", "arguments": {"question": "count rows in orders"}},
            }),
        ]
    ) + "\n"
    output = StringIO()
    try:
        run_stdio(service, reader=StringIO(incoming), writer=output)
        messages = [json.loads(line) for line in output.getvalue().splitlines()]
        assert messages[0]["result"]["capabilities"]["tools"] == {}
        names = [item["name"] for item in messages[1]["result"]["tools"]]
        assert names == ["query_analytics", "get_schema"]
        tool_result = json.loads(messages[2]["result"]["content"][0]["text"])
        assert tool_result["rows"] == [[2]]
        assert "parameters" not in tool_result
    finally:
        service.close()


def test_rest_server_requires_auth_for_non_loopback_bind(catalog: Path, tmp_path: Path) -> None:
    service = build_service(
        catalog_path=str(catalog), evidence_path=str(tmp_path / "bind-evidence.sqlite")
    )
    try:
        with pytest.raises(ValueError, match="bearer token.*TLS"):
            create_server(service, host="0.0.0.0")
        with pytest.raises(ValueError, match="max_concurrent_queries"):
            create_server(service, max_concurrent_queries=0)
        with pytest.raises(ValueError, match="max_concurrent_queries"):
            create_server(service, max_concurrent_queries=33)
    finally:
        service.close()


def test_remote_rest_requires_bearer_token_and_tls(catalog: Path, tmp_path: Path) -> None:
    service = build_service(
        catalog_path=str(catalog), evidence_path=str(tmp_path / "remote-evidence.sqlite")
    )
    try:
        with pytest.raises(ValueError, match="bearer token.*TLS"):
            create_server(service, host="0.0.0.0")
        with pytest.raises(ValueError, match="at least 32"):
            create_server(service, host="0.0.0.0", bearer_token="short",
                          tls_certfile="cert.pem", tls_keyfile="key.pem")
        with pytest.raises(ValueError, match="configured together"):
            create_server(service, host="127.0.0.1", tls_certfile="cert.pem")
    finally:
        service.close()


def test_local_rest_bearer_auth_rejects_missing_and_wrong_token(
    catalog: Path, tmp_path: Path
) -> None:
    from urllib.error import HTTPError

    service = build_service(
        catalog_path=str(catalog), evidence_path=str(tmp_path / "auth-evidence.sqlite")
    )
    server = create_server(service, port=0, bearer_token="t" * 32)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        for headers in ({}, {"Authorization": "Bearer " + "w" * 32}):
            request = Request(base + "/health", headers=headers)
            with pytest.raises(HTTPError) as error:
                urlopen(request, timeout=5)
            assert error.value.code == 401
        request = Request(base + "/health", headers={"Authorization": "Bearer " + "t" * 32})
        with urlopen(request, timeout=5) as response:
            assert json.loads(response.read())["status"] == "ok"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        service.close()



def test_readiness_fails_closed_when_evidence_journal_is_unavailable(
    catalog: Path, tmp_path: Path
) -> None:
    import sqlite3

    evidence_path = tmp_path / "unavailable-evidence.sqlite"
    service = build_service(catalog_path=str(catalog), evidence_path=str(evidence_path))
    try:
        asyncio.run(service.query("count rows in orders"))
        assert service.readiness()["status"] == "ready"
        with sqlite3.connect(evidence_path) as connection:
            connection.execute("DROP TABLE evidence_journal")
            connection.commit()
        report = service.readiness()
        assert report == {
            "status": "not_ready",
            "checks": {
                "catalog": "ok",
                "evidence_journal": "unavailable",
                "decision_trace_journal": "ok",
            },
        }
    finally:
        service.close()


def test_rest_server_closes_idle_incomplete_request_after_socket_timeout(
    catalog: Path, tmp_path: Path
) -> None:
    service = build_service(
        catalog_path=str(catalog), evidence_path=str(tmp_path / "socket-timeout.sqlite")
    )
    server = create_server(service, port=0, request_socket_timeout_seconds=1)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with socket.create_connection(("127.0.0.1", server.server_port), timeout=3) as client:
            client.sendall(b"GET /health HTTP/1.1\r\nHost: localhost\r\n")
            client.settimeout(3)
            assert client.recv(1) == b""
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        service.close()


def test_rest_server_validates_socket_timeout_bounds(catalog: Path, tmp_path: Path) -> None:
    service = build_service(
        catalog_path=str(catalog), evidence_path=str(tmp_path / "socket-timeout-bounds.sqlite")
    )
    try:
        with pytest.raises(ValueError, match="request_socket_timeout_seconds"):
            create_server(service, request_socket_timeout_seconds=0)
        with pytest.raises(ValueError, match="request_socket_timeout_seconds"):
            create_server(service, request_socket_timeout_seconds=121)
    finally:
        service.close()



def test_rest_request_logs_are_structured_and_exclude_question_payload(
    catalog: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    service = build_service(
        catalog_path=str(catalog),
        evidence_path=str(tmp_path / "redacted-log-evidence.sqlite"),
    )
    server = create_server(service, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    question = "TOP-SECRET-QUESTION-DO-NOT-LOG"
    request = Request(
        f"http://{server.server_address[0]}:{server.server_port}/v1/query",
        data=json.dumps({"question": question}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with (
            caplog.at_level(logging.INFO, logger="d2d.api"),
            urlopen(request, timeout=5) as response,
        ):
            assert response.status == 200
            response.read()
        messages = [
            record.getMessage()
            for record in caplog.records
            if record.name == "d2d.api" and record.getMessage()
        ]
        assert len(messages) == 1
        event = json.loads(messages[0])
        assert event["event"] == "http_request"
        assert event["method"] == "POST"
        assert event["route"] == "/v1/query"
        assert event["status"] == 200
        assert isinstance(event["duration_ms"], (int, float))
        assert question not in messages[0]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        service.close()


def test_rest_server_tracks_active_requests_before_close(
    catalog: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_service(
        catalog_path=str(catalog),
        evidence_path=str(tmp_path / "drain-evidence.sqlite"),
    )
    started = threading.Event()
    release = threading.Event()

    async def blocked_query(question: str) -> dict[str, object]:
        started.set()
        await asyncio.to_thread(release.wait, 5)
        return {"question": question, "rows": [[2]]}

    monkeypatch.setattr(service, "query", blocked_query)
    server = create_server(service, port=0)
    serve_thread = threading.Thread(target=server.serve_forever, daemon=True)
    serve_thread.start()
    request = Request(
        f"http://{server.server_address[0]}:{server.server_port}/v1/query",
        data=json.dumps({"question": "count rows in orders"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    request_errors: list[Exception] = []

    def send_request() -> None:
        try:
            with urlopen(request, timeout=5) as response:
                response.read()
        except Exception as exc:
            request_errors.append(exc)

    request_thread = threading.Thread(target=send_request)
    request_thread.start()
    try:
        assert started.wait(5)
        server.shutdown()
        serve_thread.join(timeout=5)
        assert not serve_thread.is_alive()
        assert not server.wait_for_idle(0.01)
        release.set()
        request_thread.join(timeout=5)
        assert not request_thread.is_alive()
        assert request_errors == []
        assert server.wait_for_idle(5)
    finally:
        release.set()
        server.server_close()
        serve_thread.join(timeout=5)
        request_thread.join(timeout=5)
        service.close()
