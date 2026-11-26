from pathlib import Path

import pytest

from d2d.core import journal_restore
from d2d.core.decision_trace_store import SQLiteDecisionTraceStore
from d2d.core.evidence_store import SQLiteEvidenceStore
from d2d.core.journal_restore import backup_paired_journals, restore_paired_journals


def _empty_journal_pair(directory: Path) -> tuple[Path, Path]:
    evidence = directory / "evidence.sqlite"
    traces = directory / "traces.sqlite"
    SQLiteEvidenceStore(evidence)
    SQLiteDecisionTraceStore(traces)
    return evidence, traces


def test_paired_backup_and_restore_round_trip_empty_genesis_journals(
    tmp_path: Path,
) -> None:
    evidence, traces = _empty_journal_pair(tmp_path)
    archive = tmp_path / "archive"

    backup = backup_paired_journals(evidence, traces, archive)
    restored = restore_paired_journals(
        archive / "evidence",
        archive / "traces",
        tmp_path / "restored",
    )

    assert backup.evidence_records == 0
    assert backup.trace_records == 0
    assert backup.traces_evidence_checked == 0
    assert restored.evidence_records_restored == 0
    assert restored.trace_records_restored == 0
    assert SQLiteEvidenceStore(tmp_path / "restored" / "evidence.sqlite").verify().valid
    assert SQLiteDecisionTraceStore(tmp_path / "restored" / "decision-traces.sqlite").verify().valid


def test_paired_backup_manifest_fsync_failure_does_not_publish_or_leave_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence, traces = _empty_journal_pair(tmp_path)
    destination = tmp_path / "backup-fsync-failure"

    real_fsync_file = journal_restore._fsync_file

    def fail_manifest_fsync(path: Path) -> None:
        if path.name == "segment-000001.manifest.json":
            raise OSError("simulated backup manifest fsync failure")
        real_fsync_file(path)

    monkeypatch.setattr(journal_restore, "_fsync_file", fail_manifest_fsync)

    with pytest.raises(OSError, match="backup manifest fsync failure"):
        backup_paired_journals(evidence, traces, destination)

    assert not destination.exists()
    assert not list(tmp_path.glob(".backup-fsync-failure.backup-*"))


def test_paired_backup_no_replace_race_preserves_operator_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence, traces = _empty_journal_pair(tmp_path)
    destination = tmp_path / "backup-race"

    def race_with_operator_directory(source: Path, target: Path) -> None:
        target.mkdir()
        (target / "operator.txt").write_text("preserve", encoding="utf-8")
        raise FileExistsError(target)

    monkeypatch.setattr(
        journal_restore, "_rename_directory_no_replace", race_with_operator_directory
    )

    with pytest.raises(FileExistsError):
        backup_paired_journals(evidence, traces, destination)

    assert (destination / "operator.txt").read_text(encoding="utf-8") == "preserve"
    assert not list(tmp_path.glob(".backup-race.backup-*"))


def test_paired_backup_parent_fsync_failure_keeps_complete_published_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence, traces = _empty_journal_pair(tmp_path)
    destination = tmp_path / "backup-parent-fsync-failure"
    real_fsync_directory = journal_restore._fsync_directory

    def fail_parent_fsync(path: Path) -> None:
        if path == destination.parent:
            raise OSError("simulated backup parent directory fsync failure")
        real_fsync_directory(path)

    monkeypatch.setattr(journal_restore, "_fsync_directory", fail_parent_fsync)

    with pytest.raises(OSError, match="backup parent directory fsync failure"):
        backup_paired_journals(evidence, traces, destination)

    assert (destination / "evidence" / "segment-000001.sqlite").is_file()
    assert (destination / "evidence" / "segment-000001.manifest.json").is_file()
    assert (destination / "traces" / "segment-000001.sqlite").is_file()
    assert (destination / "traces" / "segment-000001.manifest.json").is_file()
    assert not list(tmp_path.glob(".backup-parent-fsync-failure.backup-*"))
