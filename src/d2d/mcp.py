"""Dependency-free MCP stdio server exposing only the bounded copilot runtime."""
from __future__ import annotations

import asyncio
import json
import sys
from typing import TextIO

from d2d.application import CopilotService


def run_stdio(
    service: CopilotService, *, reader: TextIO | None = None, writer: TextIO | None = None,
) -> None:
    """Serve newline-delimited JSON-RPC MCP messages over stdio."""
    source = reader or sys.stdin
    sink = writer or sys.stdout
    for line in source:
        request: object = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                continue
            method = request.get("method")
            request_id = request.get("id")
            if method == "notifications/initialized":
                continue
            if method == "initialize":
                result: object = {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "data-to-decision-copilot", "version": "0.1.0"},
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {
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
                            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
                        },
                    ]
                }
            elif method == "tools/call":
                params = request.get("params")
                if not isinstance(params, dict):
                    raise ValueError("invalid tool parameters")
                name = params.get("name")
                arguments = params.get("arguments", {})
                if not isinstance(arguments, dict):
                    raise ValueError("invalid tool arguments")
                if name == "get_schema":
                    payload: object = service.schema()
                elif name == "query_analytics" and isinstance(arguments.get("question"), str):
                    payload = asyncio.run(service.query(arguments["question"]))
                else:
                    raise ValueError("unknown tool or invalid arguments")
                result = {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, default=str)}]}
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
