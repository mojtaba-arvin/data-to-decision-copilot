"""Command-line entry point for local query, REST, and MCP workflows."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import asyncio
import json
import os
from collections.abc import Sequence
from pathlib import Path

from d2d.api import create_server
from d2d.application import build_service
from d2d.demo import create_demo_catalog
from d2d.core.journal_restore import restore_paired_journals
from d2d.mcp import run_stdio


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="d2d", description="Evidence-backed read-only analytics")
    subparsers = parser.add_subparsers(dest="command", required=True)
    journal = subparsers.add_parser(
        "journal", help="Verify and restore paired Evidence and DecisionTrace archives"
    )
    journal_subparsers = journal.add_subparsers(dest="journal_action", required=True)
    restore = journal_subparsers.add_parser(
        "restore", help="Restore both journal chains into one fresh directory"
    )
    restore.add_argument("--evidence-archive", type=Path, required=True)
    restore.add_argument("--trace-archive", type=Path, required=True)
    restore.add_argument("--output", type=Path, required=True)

    for name in ("query", "serve", "mcp", "schema", "demo"):
        command = subparsers.add_parser(name)
        if name == "demo":
            command.add_argument(
                "--output", type=Path, default=Path("analytics.duckdb"),
                help="New catalog path (must not already exist)",
            )
            continue
        command.add_argument("--catalog", required=True, help="DuckDB catalog path")
        command.add_argument("--evidence-db", default="d2d-evidence.sqlite")
        command.add_argument("--history-db", default="d2d-history.sqlite")
        command.add_argument("--provider", choices=("rules", "ollama"), default="rules")
        command.add_argument("--model", default="qwen3:8b")
        command.add_argument(
            "--isolated-worker", action="store_true",
            help="Execute each DuckDB query in a resource-limited child process (POSIX only)",
        )
        if name == "query":
            command.add_argument("--question", required=True)
        if name == "serve":
            command.add_argument("--host", default="127.0.0.1")
            command.add_argument("--port", type=int, default=8765)
            command.add_argument(
                "--bearer-token-env", default="D2D_REST_BEARER_TOKEN",
                help="Environment variable containing a 32+ character bearer token for authenticated access",
            )
            command.add_argument("--tls-certfile", help="PEM server certificate (required for remote binds)")
            command.add_argument("--tls-keyfile", help="PEM private key (required for remote binds)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "demo":
        destination = create_demo_catalog(args.output)
        print(f"Created synthetic demo catalog: {destination}")
        return 0
    if args.command == "journal":
        report = restore_paired_journals(
            args.evidence_archive,
            args.trace_archive,
            args.output,
        )
        print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
        return 0

    service = build_service(
        catalog_path=args.catalog,
        evidence_path=args.evidence_db,
        history_path=args.history_db,
        provider=args.provider,
        model=args.model,
        isolated_worker=args.isolated_worker,
    )
    try:
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
            server = create_server(
                service, host=args.host, port=args.port,
                bearer_token=os.environ.get(args.bearer_token_env),
                tls_certfile=args.tls_certfile, tls_keyfile=args.tls_keyfile,
            )
            try:
                scheme = "https" if args.tls_certfile else "http"
                print(f"REST API listening on {scheme}://{args.host}:{args.port}")
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
            return 0
    finally:
        service.close()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
