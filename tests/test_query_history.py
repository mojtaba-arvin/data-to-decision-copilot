from pathlib import Path

import pytest

from d2d.query_history import QueryHistoryItem, SQLiteQueryHistoryStore


def item(index: int) -> QueryHistoryItem:
    return QueryHistoryItem(
        question=f"count rows in table_{index}",
        evidence_id=f"evidence-{index}",
        evidence_sequence=index,
        schema_fingerprint=f"{index:064x}",
        planner_reference=f"planner@1#{index}",
        created_at=f"2026-11-22T00:00:{index:02d}+00:00",
    )


def test_query_history_survives_restart_and_enforces_retention(tmp_path: Path) -> None:
    path = tmp_path / "history.sqlite"
    first = SQLiteQueryHistoryStore(path, max_records=2)
    first.append(item(1))
    first.append(item(2))
    first.append(item(3))
    first.close()

    reopened = SQLiteQueryHistoryStore(path, max_records=2)
    try:
        records = reopened.list(limit=2)
        assert [record.evidence_id for record in records] == ["evidence-3", "evidence-2"]
    finally:
        reopened.close()


def test_query_history_rejects_out_of_bound_history_limit(tmp_path: Path) -> None:
    store = SQLiteQueryHistoryStore(tmp_path / "history.sqlite", max_records=2)
    try:
        with pytest.raises(ValueError, match="history limit"):
            store.list(limit=3)
    finally:
        store.close()
