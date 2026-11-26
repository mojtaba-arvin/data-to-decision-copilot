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


def test_query_history_survives_restart_with_explicit_retention(tmp_path: Path) -> None:
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
        with pytest.raises(ValueError, match="retention bound"):
            store.list(limit=3)
    finally:
        store.close()


def test_query_history_retains_all_records_by_default(tmp_path: Path) -> None:
    store = SQLiteQueryHistoryStore(tmp_path / "unbounded-history.sqlite")
    try:
        for index in range(1, 1_002):
            store.append(item(index))
        records = store.list(limit=1_000)
        assert len(records) == 1_000
        assert records[0].evidence_id == "evidence-1001"
        assert records[-1].evidence_id == "evidence-2"
        assert store.max_records is None
    finally:
        store.close()


def test_query_history_rejects_invalid_opt_in_retention(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="max_records"):
        SQLiteQueryHistoryStore(tmp_path / "invalid-history.sqlite", max_records=0)


def test_query_history_backup_is_verified_and_independent(tmp_path: Path) -> None:
    source_path = tmp_path / "history.sqlite"
    backup_path = tmp_path / "backup" / "history.sqlite"
    store = SQLiteQueryHistoryStore(source_path)
    try:
        store.append(item(1))
        store.append(item(2))
        verification = store.verify()
        assert verification.valid
        assert verification.records_checked == 2

        report = store.backup(backup_path)
        assert report.records == 2
        assert report.bytes == backup_path.stat().st_size
        assert len(report.sha256) == 64

        store.append(item(3))
        restored = SQLiteQueryHistoryStore(backup_path)
        try:
            assert [record.evidence_id for record in restored.list(limit=10)] == [
                "evidence-2", "evidence-1",
            ]
            assert restored.verify().valid
        finally:
            restored.close()
    finally:
        store.close()


def test_query_history_backup_refuses_overwrite(tmp_path: Path) -> None:
    store = SQLiteQueryHistoryStore(tmp_path / "history.sqlite")
    destination = tmp_path / "backup.sqlite"
    destination.write_bytes(b"operator data")
    try:
        with pytest.raises(FileExistsError):
            store.backup(destination)
        assert destination.read_bytes() == b"operator data"
    finally:
        store.close()


def test_query_history_verification_detects_corruption(tmp_path: Path) -> None:
    path = tmp_path / "history.sqlite"
    store = SQLiteQueryHistoryStore(path)
    store.append(item(1))
    store.close()

    with path.open("ab") as stream:
        stream.write(b"not-a-valid-sqlite-page")

    report = SQLiteQueryHistoryStore(path).verify()
    assert not report.valid
