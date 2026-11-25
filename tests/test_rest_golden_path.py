from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import duckdb

from d2d.api import create_server
from d2d.application import build_service


def _get_json(url: str) -> Any:
    with urlopen(url, timeout=5) as response:
        return json.loads(response.read())


def _post_json(url: str, payload: dict[str, object]) -> Any:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read())


def test_rest_golden_path_persists_and_replays_human_decision_trace(
    tmp_path: Path,
) -> None:
    catalog = tmp_path / "analytics.duckdb"
    with duckdb.connect(str(catalog)) as connection:
        connection.execute("CREATE TABLE orders (id INTEGER, amount INTEGER)")
        connection.execute("INSERT INTO orders VALUES (1, 10), (2, 20)")

    service = build_service(
        catalog_path=str(catalog),
        evidence_path=str(tmp_path / "evidence.sqlite"),
        history_path=str(tmp_path / "history.sqlite"),
        trace_path=str(tmp_path / "traces.sqlite"),
    )
    server = create_server(service, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    base = f"http://{host}:{port}"

    try:
        schema = _get_json(base + "/v1/schema")
        tables = schema["tables"]
        assert isinstance(tables, list)
        assert tables[0]["name"] == "orders"

        query = _post_json(base + "/v1/query", {"question": "count rows in orders"})
        evidence_id = query["evidence_id"]
        assert query["rows"] == [[2]]
        assert isinstance(evidence_id, str)

        decision = _post_json(
            base + "/v1/decision",
            {
                "question": "count rows in orders",
                "evidence_id": evidence_id,
                "decision": {
                    "action": "Review order volume before changing inventory.",
                    "rationale": (
                        "The executed query returned two order rows; this alone "
                        "does not establish a trend."
                    ),
                    "evidence_ids": [evidence_id],
                    "confidence": "medium",
                    "alternatives": [{
                        "action": "Keep the current inventory plan.",
                        "trade_off": "Avoids a premature change but may delay a response.",
                    }],
                    "reversal_conditions": [
                        "Reassess after a comparable time-window query."
                    ],
                    "caveats": [
                        "Only a row count was queried; no revenue or trend was measured."
                    ],
                },
                "claims": [{
                    "claim_id": "observed-row-count",
                    "statement": "The query returned 2 rows.",
                    "evidence_ids": [evidence_id],
                    "kind": "fact",
                }],
            },
        )
        trace: dict[str, Any] = decision["trace"]
        trace_id: str = trace["trace_id"]
        assert isinstance(trace_id, str)
        assert decision["proposal_origin"] == "caller_supplied"
        assert decision["policy"]["allowed"] is True

        listed = _get_json(base + "/v1/traces")
        items: list[dict[str, Any]] = listed["items"]
        assert any(item["trace_id"] == trace_id for item in items)

        replayed = _get_json(base + f"/v1/traces/{trace_id}")
        assert replayed["trace_id"] == trace_id
        assert replayed["evidence"][0]["evidence_id"] == evidence_id
        assert replayed["decision"]["action"] == trace["decision"]["action"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        service.close()
