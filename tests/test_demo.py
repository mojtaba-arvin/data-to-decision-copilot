from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import pytest

from d2d.application import build_service
from d2d.cli import main
from d2d.demo import create_demo_catalog
from d2d.diagnostics import inspect_storage


def test_demo_catalog_contains_only_expected_synthetic_sales(tmp_path: Path) -> None:
    destination = tmp_path / "nested" / "analytics.duckdb"

    created = create_demo_catalog(destination)

    assert created == destination.resolve()
    connection = duckdb.connect(str(created), read_only=True)
    try:
        assert connection.execute(
            "SELECT order_id, region, amount FROM sales ORDER BY order_id"
        ).fetchall() == [
            (1, "north", 125.50),
            (2, "south", 80.00),
            (3, "north", 210.25),
            (4, "west", 44.75),
        ]
    finally:
        connection.close()


def test_demo_catalog_refuses_to_overwrite_existing_file(tmp_path: Path) -> None:
    destination = tmp_path / "important.duckdb"
    original = b"existing user data"
    destination.write_bytes(original)

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        create_demo_catalog(destination)

    assert destination.read_bytes() == original
    assert list(tmp_path.iterdir()) == [destination]


def test_demo_cli_creates_catalog_without_opening_a_service(tmp_path: Path) -> None:
    destination = tmp_path / "cli-demo.duckdb"

    assert main(["demo", "--output", str(destination)]) == 0

    connection = duckdb.connect(str(destination), read_only=True)
    try:
        assert connection.execute("SELECT count(*) FROM sales").fetchone() == (4,)
    finally:
        connection.close()


def test_demo_catalog_completes_query_to_evidence_journey(tmp_path: Path) -> None:
    catalog = create_demo_catalog(tmp_path / "sales.duckdb")
    service = build_service(
        catalog_path=str(catalog),
        evidence_path=str(tmp_path / "evidence.sqlite"),
        history_path=str(tmp_path / "history.sqlite"),
        trace_path=str(tmp_path / "traces.sqlite"),
    )
    try:
        result = asyncio.run(service.query("count rows in sales"))
        assert result["rows"] == [[4]]
        assert result["truncated"] is False
        assert result["evidence_id"]
        assert service.history()[0]["evidence_id"] == result["evidence_id"]
        assert service.readiness()["status"] == "ready"
    finally:
        service.close()


def test_storage_diagnostics_reports_capacity_without_creating_data_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import d2d.diagnostics as diagnostics

    def fake_disk_usage(_path: str | Path) -> Any:
        return SimpleNamespace(total=1_000, used=900, free=100)

    monkeypatch.setattr(diagnostics.shutil, "disk_usage", fake_disk_usage)
    paths = {
        "catalog": tmp_path / "catalog.duckdb",
        "evidence": tmp_path / "evidence.sqlite",
    }

    report = inspect_storage(paths)

    assert report["status"] == "warning"
    assert [item["status"] for item in report["filesystems"]] == ["warning", "warning"]
    assert all(item["free_bytes"] == 100 for item in report["filesystems"])
    assert not (tmp_path / "catalog.duckdb").exists()
    assert not (tmp_path / "evidence.sqlite").exists()


def test_storage_diagnostics_rejects_invalid_thresholds(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="thresholds"):
        inspect_storage(
            {"catalog": tmp_path / "catalog.duckdb"},
            warning_free_percent=5,
            critical_free_percent=10,
        )


def test_doctor_cli_returns_nonzero_for_critical_capacity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import d2d.diagnostics as diagnostics

    def fake_disk_usage(_path: str | Path) -> Any:
        return SimpleNamespace(total=1_000, used=970, free=30)

    monkeypatch.setattr(diagnostics.shutil, "disk_usage", fake_disk_usage)
    result = main(["doctor", "--catalog", str(tmp_path / "analytics.duckdb")])

    output = json.loads(capsys.readouterr().out)
    assert result == 1
    assert output["status"] == "critical"
    assert output["filesystems"][0]["free_percent"] == 3.0
