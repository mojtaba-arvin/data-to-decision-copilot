from __future__ import annotations

import asyncio
import json
import threading
from io import StringIO
from pathlib import Path
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
        assert result.get("parameter_count") == 0
        assert service.history()[0]["question"] == "count rows in orders"
        assert '"name": "orders"' in json.dumps(service.schema())
    finally:
        service.close()


def test_rest_api_serves_query_history_and_schema(catalog: Path, tmp_path: Path) -> None:
    service = build_service(
        catalog_path=str(catalog), evidence_path=str(tmp_path / "api-evidence.sqlite")
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
        with urlopen(base + "/v1/history", timeout=5) as response:
            history = json.loads(response.read())
        assert history["items"][0]["evidence_id"] == payload["evidence_id"]
        with urlopen(base + "/v1/schema", timeout=5) as response:
            schema = json.loads(response.read())
        assert schema["tables"][0]["name"] == "orders"
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
    finally:
        service.close()


def test_rest_server_refuses_non_loopback_bind(catalog: Path, tmp_path: Path) -> None:
    service = build_service(
        catalog_path=str(catalog), evidence_path=str(tmp_path / "bind-evidence.sqlite")
    )
    try:
        with pytest.raises(ValueError, match="loopback"):
            create_server(service, host="0.0.0.0")
    finally:
        service.close()
