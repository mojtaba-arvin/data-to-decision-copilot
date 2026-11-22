"""Command-line entry point for local query, REST, and MCP workflows."""
from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence

from d2d.api import create_server
from d2d.application import build_service
from d2d.mcp import run_stdio


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="d2d", description="Evidence-backed read-only analytics")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("query", "serve", "mcp", "schema"):
        command = subparsers.add_parser(name)
        command.add_argument("--catalog", required=True, help="DuckDB catalog path")
        command.add_argument("--evidence-db", default="d2d-evidence.sqlite")
        command.add_argument("--provider", choices=("rules", "ollama"), default="rules")
        command.add_argument("--model", default="qwen3:8b")
        if name == "query":
            command.add_argument("--question", required=True)
        if name == "serve":
            command.add_argument("--host", default="127.0.0.1")
            command.add_argument("--port", type=int, default=8765)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    service = build_service(
        catalog_path=args.catalog,
        evidence_path=args.evidence_db,
        provider=args.provider,
        model=args.model,
    )
    if args.command == "query":
        result = asyncio.run(service.query(args.question))
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    if args.command == "schema":
        print(json.dumps(service.schema(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "mcp":
        run_stdio(service)
        return 0
    if args.command == "serve":
        server = create_server(service, host=args.host, port=args.port)
        try:
            print(f"REST API listening on http://{args.host}:{args.port}")
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
