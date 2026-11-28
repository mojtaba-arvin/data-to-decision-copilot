from __future__ import annotations

import json
from pathlib import Path

import pytest

from d2d.core.decision_trace_store import SQLiteDecisionTraceStore
from d2d.core.evidence_store import SQLiteEvidenceStore
from d2d.core.runtime_recovery import backup_runtime, restore_runtime
from d2d.demo import create_demo_catalog
from d2d.query_history import QueryHistoryItem, SQLiteQueryHistoryStore


def _runtime(tmp_path: Path) -> dict[str, Path]:
    catalog = tmp_path / "catalog.duckdb"
    create_demo_catalog(catalog)
    evidence = tmp_path / "evidence.sqlite"
    trace = tmp_path / "traces.sqlite"
    history = tmp_path / "history.sqlite"
    SQLiteEvidenceStore(evidence)
    SQLiteDecisionTraceStore(trace)
    store = SQLiteQueryHistoryStore(history)
    store.append(
        QueryHistoryItem(
            question="count rows in orders",
            evidence_id="evidence-demo",
            evidence_sequence=1,
            schema_fingerprint="a" * 64,
            planner_reference="rules@1",
            created_at="2026-11-28T00:00:00+00:00",
        )
    )
    store.close()
    return {
        "catalog": catalog,
        "evidence": evidence,
        "trace": trace,
        "history": history,
    }


def test_full_runtime_backup_restore_verifies_all_four_databases(tmp_path: Path) -> None:
    sources = _runtime(tmp_path)
    bundle = tmp_path / "backup"
    restored = tmp_path / "restored"

    backup = backup_runtime(
        catalog=sources["catalog"],
        evidence_db=sources["evidence"],
        trace_db=sources["trace"],
        history_db=sources["history"],
        output=bundle,
        provider="rules",
    )
    result = restore_runtime(bundle, restored)

    assert backup.artifacts_verified == 4
    assert result.artifacts_verified == 4
    assert result.catalog_schema_fingerprint == backup.catalog_schema_fingerprint
    assert result.evidence_head_sha256 == backup.evidence_head_sha256
    assert result.decision_trace_head_sha256 == backup.decision_trace_head_sha256
    assert result.query_history_records == 1
    assert result.provider == "rules"
    assert {path.name for path in restored.iterdir()} == {
        "catalog.duckdb",
        "evidence.sqlite",
        "decision-traces.sqlite",
        "query-history.sqlite",
    }
    history = SQLiteQueryHistoryStore(restored / "query-history.sqlite")
    try:
        assert history.list(limit=1)[0].question == "count rows in orders"
    finally:
        history.close()


def test_runtime_bundle_manifest_excludes_secrets_and_is_canonical(tmp_path: Path) -> None:
    sources = _runtime(tmp_path)
    bundle = tmp_path / "backup"
    backup_runtime(
        catalog=sources["catalog"],
        evidence_db=sources["evidence"],
        trace_db=sources["trace"],
        history_db=sources["history"],
        output=bundle,
        provider="ollama",
        model="qwen3:8b",
    )
    raw = (bundle / "manifest.json").read_text(encoding="utf-8")
    manifest = json.loads(raw)
    assert manifest["runtime"] == {
        "provider": "ollama",
        "model": "qwen3:8b",
        "history_max_records": None,
    }
    assert "token" not in raw.lower()
    assert "secret" not in raw.lower()
    assert "private_key" not in raw.lower()


def test_restore_rejects_tampered_artifact_without_publishing(tmp_path: Path) -> None:
    sources = _runtime(tmp_path)
    bundle = tmp_path / "backup"
    backup_runtime(
        catalog=sources["catalog"],
        evidence_db=sources["evidence"],
        trace_db=sources["trace"],
        history_db=sources["history"],
        output=bundle,
    )
    with (bundle / "query-history.sqlite").open("ab") as stream:
        stream.write(b"tamper")

    destination = tmp_path / "must-not-exist"
    with pytest.raises(ValueError, match="digest or size mismatch"):
        restore_runtime(bundle, destination)
    assert not destination.exists()


def test_backup_and_restore_refuse_existing_destinations(tmp_path: Path) -> None:
    sources = _runtime(tmp_path)
    bundle = tmp_path / "backup"
    bundle.mkdir()
    with pytest.raises(FileExistsError):
        backup_runtime(
            catalog=sources["catalog"],
            evidence_db=sources["evidence"],
            trace_db=sources["trace"],
            history_db=sources["history"],
            output=bundle,
        )

    restore_target = tmp_path / "existing"
    restore_target.mkdir()
    with pytest.raises(FileExistsError):
        restore_runtime(bundle, restore_target)


def test_backup_rejects_malformed_sqlite_schema_without_repairing_it(tmp_path: Path) -> None:
    import sqlite3

    sources = _runtime(tmp_path)
    with sqlite3.connect(sources["history"]) as connection:
        connection.execute("DROP TABLE query_history")
    destination = tmp_path / "must-not-publish"
    with pytest.raises(ValueError, match="source query-history verification failed"):
        backup_runtime(
            catalog=sources["catalog"],
            evidence_db=sources["evidence"],
            trace_db=sources["trace"],
            history_db=sources["history"],
            output=destination,
        )
    assert not destination.exists()
    with sqlite3.connect(sources["history"]) as connection:
        columns = connection.execute("PRAGMA table_info(query_history)").fetchall()
    assert columns == []
