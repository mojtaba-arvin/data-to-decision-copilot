"""Dependency-free MCP stdio server exposing only the bounded copilot runtime."""
from __future__ import annotations

import asyncio
import json
import sys
from typing import TextIO, cast

from d2d.application import CopilotService


def _tools_list_result() -> object:
    return {
        "tools": [
            {
                "name": "query_analytics",
                "description": "Run a bounded, read-only natural-language analytics query.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"question": {"type": "string", "minLength": 1}},
                    "required": ["question"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_schema",
                "description": "Return the discovered schema and its fingerprint.",
                "inputSchema": {
                    "type": "object",
                    "properties": cast(dict[str, object], {}),
                    "additionalProperties": False,
                },
            },
        ]
    }


def _content_result(payload: object) -> object:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False, default=str),
            }
        ]
    }


def run_stdio(
    service: CopilotService, *, reader: TextIO | None = None, writer: TextIO | None = None,
) -> None:
    """Serve newline-delimited JSON-RPC MCP messages over stdio."""
    source = reader or sys.stdin
    sink = writer or sys.stdout
    for line in source:
        request: object = None
        try:
            decoded: object = json.loads(line)
            if not isinstance(decoded, dict):
                continue
            request = cast(dict[str, object], decoded)
            method = request.get("method")
            request_id = request.get("id")
            result: object = None
            if method == "notifications/initialized":
                continue
            if method == "initialize":
                result = {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "data-to-decision-copilot", "version": "0.1.0"},
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = _tools_list_result()
            elif method == "tools/call":
                params_value = request.get("params")
                if not isinstance(params_value, dict):
                    raise ValueError("invalid tool parameters")
                params = cast(dict[str, object], params_value)
                name = params.get("name")
                arguments_value = params.get("arguments", {})
                if not isinstance(arguments_value, dict):
                    raise ValueError("invalid tool arguments")
                arguments = cast(dict[str, object], arguments_value)
                if name == "get_schema":
                    payload: object = service.schema()
                elif name == "query_analytics" and isinstance(arguments.get("question"), str):
                    payload = asyncio.run(service.query(cast(str, arguments["question"])))
                else:
                    raise ValueError("unknown tool or invalid arguments")
                result = _content_result(payload)
            else:
                if request_id is None:
                    continue
                sink.write(json.dumps({
                    "jsonrpc": "2.0", "id": request_id,
                    "error": {"code": -32601, "message": "Method not found"},
                }) + "\n")
                sink.flush()
                continue
            if request_id is not None:
                sink.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}) + "\n")
                sink.flush()
        except Exception as exc:
            request_id = request.get("id") if isinstance(request, dict) else None
            if request_id is not None:
                sink.write(json.dumps({
                    "jsonrpc": "2.0", "id": request_id,
                    "error": {"code": -32000, "message": str(exc)},
                }) + "\n")
                sink.flush()
