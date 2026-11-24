"""Server-side construction of immutable execution-derived evidence."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from d2d.core.contracts import Evidence, EvidenceStatus
from d2d.core.sql_executor import QueryResult
from d2d.core.sql_guard import validate_read_only_sql


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        allow_nan=False,
    ).encode("utf-8")


def create_execution_evidence(
    *,
    sql: str,
    dialect: str,
    result: QueryResult,
    source_id: str,
    schema_fingerprint: str,
    execution_ms: float,
    data_version: str | None = None,
) -> Evidence:
    """Build evidence from executor output; never accept model-authored evidence.

    Parameter values are intentionally not retained or hashed: low-entropy
    secrets and identifiers can be recovered from unsalted hashes.
    """
    if not source_id.strip():
        raise ValueError("source_id must not be empty")
    if not schema_fingerprint.strip():
        raise ValueError("schema_fingerprint must not be empty")
    if execution_ms < 0:
        raise ValueError("execution_ms must not be negative")

    validated = validate_read_only_sql(sql, dialect=dialect)
    columns = result.columns
    rows = result.rows
    if result.captured_rows != len(rows):
        raise ValueError("executor captured_rows does not match its returned rows")
    if any(len(row) != len(columns) for row in rows):
        raise ValueError("executor row width does not match returned columns")

    query_bytes = validated.sql.encode("utf-8")
    result_payload = {
        "columns": columns,
        "rows": rows,
        "captured_rows": result.captured_rows,
        "output_bytes": result.output_bytes,
        "truncated": result.truncated,
    }
    return Evidence(
        evidence_id=str(uuid.uuid4()),
        sql=validated.sql,
        dialect=validated.dialect,
        status=EvidenceStatus.EXECUTED,
        columns=columns,
        rows=rows,
        row_count=result.captured_rows,
        execution_ms=execution_ms,
        data_version=data_version,
        source_id=source_id,
        schema_fingerprint=schema_fingerprint,
        query_sha256=hashlib.sha256(query_bytes).hexdigest(),
        result_sha256=hashlib.sha256(_canonical_json(result_payload)).hexdigest(),
        executed_at=datetime.now(UTC),
        output_bytes=result.output_bytes,
        truncated=result.truncated,
    )
