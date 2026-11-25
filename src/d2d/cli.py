"""Command-line entry point for local query, REST, and MCP workflows."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
from collections.abc import Sequence
from pathlib import Path
from threading import Thread

from d2d.api import create_server
from d2d.application import build_service
from d2d.demo import create_demo_catalog
from d2d.mcp import run_stdio


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="d2d", description="Evidence-backed read-only analytics")
    subparsers = parser.add_subparsers(dest="command", required=True)
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
                "--shutdown-drain-warning-seconds", type=float, default=10.0,
                help="Time before warning that active REST requests are still draining",
            )
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
            if not 0.0 <= args.shutdown_drain_warning_seconds <= 300.0:
                raise ValueError("shutdown drain warning must be between 0 and 300 seconds")
            server = create_server(
                service, host=args.host, port=args.port,
                bearer_token=os.environ.get(args.bearer_token_env),
                tls_certfile=args.tls_certfile, tls_keyfile=args.tls_keyfile,
            )
            logging.basicConfig(level=logging.INFO, format="%(message)s")
            serve_thread = Thread(
                target=server.serve_forever, name="d2d-rest-acceptor", daemon=True
            )
            previous_sigterm = signal.signal(signal.SIGTERM, signal.default_int_handler)
            try:
                serve_thread.start()
                scheme = "https" if args.tls_certfile else "http"
                print(f"REST API listening on {scheme}://{args.host}:{args.port}")
                while serve_thread.is_alive():
                    serve_thread.join(timeout=0.5)
            except KeyboardInterrupt:
                pass
            finally:
                try:
                    if serve_thread.is_alive():
                        server.shutdown()
                        serve_thread.join(timeout=5.0)
                    drained = (
                        not serve_thread.is_alive()
                        and server.wait_for_idle(args.shutdown_drain_warning_seconds)
                    )
                    server.server_close()
                    if not drained:
                        logging.getLogger("d2d.shutdown").warning(
                            json.dumps({
                                "event": "shutdown_drain_exceeded_warning_threshold",
                                "active_requests": "at_least_one",
                                "warning_after_seconds": args.shutdown_drain_warning_seconds,
                                "policy": "wait_for_requests_before_closing_service",
                            }, separators=(",", ":"))
                        )
                finally:
                    signal.signal(signal.SIGTERM, previous_sigterm)
            return 0 if drained else 1
    finally:
        service.close()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
