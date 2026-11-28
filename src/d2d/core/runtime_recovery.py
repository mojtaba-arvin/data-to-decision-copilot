"""Versioned full runtime recovery bundles for a stopped single-tenant deployment.

The bundle contains the materialized DuckDB catalog and the three application
SQLite databases. Source inputs, credentials, TLS keys, host configuration and
model weights remain external dependencies.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import shutil
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import duckdb

from d2d.connectors.duckdb import DuckDBConnector
from d2d.core.decision_trace_store import SCHEMA_VERSION as TRACE_SCHEMA_VERSION
from d2d.core.decision_trace_store import SQLiteDecisionTraceStore
from d2d.core.evidence_store import SCHEMA_VERSION as EVIDENCE_SCHEMA_VERSION
from d2d.core.evidence_store import SQLiteEvidenceStore
from d2d.core.journal_restore import (
    fsync_directory,
    fsync_file,
    rename_directory_no_replace,
    validate_source_database,
)
from d2d.query_history import SQLiteQueryHistoryStore, verify_history_database

FORMAT_VERSION = 1
HISTORY_SCHEMA_VERSION = 1
ARTIFACTS = {
    "catalog": "catalog.duckdb",
    "evidence": "evidence.sqlite",
    "decision_trace": "decision-traces.sqlite",
    "query_history": "query-history.sqlite",
}
MANIFEST_NAME = "manifest.json"
GENESIS = "0" * 64


@dataclass(frozen=True, slots=True)
class RuntimeRecoveryReport:
    """Verified recovery bundle result."""

    destination: str
    format_version: int
    package_version: str
    artifacts_verified: int
    catalog_schema_fingerprint: str
    evidence_records: int
    evidence_head_sha256: str
    decision_trace_records: int
    decision_trace_head_sha256: str
    traces_evidence_checked: int
    query_history_records: int
    provider: str
    model: str
    history_max_records: int | None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _package_version() -> str:
    try:
        return importlib.metadata.version("data-to-decision-copilot")
    except importlib.metadata.PackageNotFoundError:
        return "0.1.0"


def _regular_file(value: str | Path, label: str) -> Path:
    supplied = Path(value)
    if supplied.is_symlink() or not supplied.is_file():
        raise ValueError(f"{label} must be an existing regular file")
    return supplied.resolve(strict=True)


def _sqlite_integrity(path: Path) -> None:
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as connection:
        row = connection.execute("PRAGMA integrity_check").fetchone()
    if row is None or row[0] != "ok":
        raise ValueError("SQLite integrity_check failed")


def _journal_head(path: Path, table: str) -> tuple[int, str]:
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as connection:
        row = connection.execute(
            f"SELECT sequence, entry_sha256 FROM {table} ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        count = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    return count, GENESIS if row is None else str(row[1])


def _schema_fingerprint(path: Path) -> str:
    with DuckDBConnector(path) as connector:
        return connector.snapshot().fingerprint


def _copy_durable(source: Path, destination: Path) -> None:
    shutil.copyfile(source, destination)
    fsync_file(destination)


def _manifest_digest(document: dict[str, object]) -> str:
    payload = {key: value for key, value in document.items() if key != "manifest_sha256"}
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _verify_sqlite_stores(directory: Path) -> tuple[int, str, int, str, int, int]:
    evidence_path = directory / ARTIFACTS["evidence"]
    trace_path = directory / ARTIFACTS["decision_trace"]
    history_path = directory / ARTIFACTS["query_history"]
    for path in (evidence_path, trace_path, history_path):
        _sqlite_integrity(path)
    validate_source_database(
        evidence_path, journal_kind="evidence", schema_version=EVIDENCE_SCHEMA_VERSION
    )
    validate_source_database(
        trace_path, journal_kind="decision_trace", schema_version=TRACE_SCHEMA_VERSION
    )
    history_report = verify_history_database(history_path)
    if not history_report.valid:
        raise ValueError("query-history verification failed")

    evidence = SQLiteEvidenceStore(evidence_path)
    trace = SQLiteDecisionTraceStore(trace_path)
    evidence_report = evidence.verify()
    if not evidence_report.valid:
        raise ValueError("evidence journal verification failed")
    trace_report = trace.verify(evidence_store=evidence)
    if not trace_report.valid:
        raise ValueError("decision-trace journal or evidence reference verification failed")
    evidence_count, evidence_head = _journal_head(evidence_path, "evidence_journal")
    trace_count, trace_head = _journal_head(trace_path, "decision_trace_journal")
    return (
        evidence_count, evidence_head, trace_count, trace_head,
        history_report.records_checked, trace_report.evidence_checked,
    )


def _validate_runtime(provider: str, model: str, history_max_records: int | None) -> None:
    if provider not in {"rules", "ollama"}:
        raise ValueError("provider must be 'rules' or 'ollama'")
    if not model or len(model) > 200 or any(ord(char) < 32 for char in model):
        raise ValueError("model identifier is invalid")
    if history_max_records is not None and (
        type(history_max_records) is not int or history_max_records < 1
    ):
        raise ValueError("history_max_records must be a positive integer when configured")


def backup_runtime(
    *,
    catalog: str | Path,
    evidence_db: str | Path,
    trace_db: str | Path,
    history_db: str | Path,
    output: str | Path,
    provider: str = "rules",
    model: str = "qwen3:8b",
    history_max_records: int | None = None,
) -> RuntimeRecoveryReport:
    """Create a verified full runtime bundle. The serving process must be stopped.

    Opening the catalog for an exclusive writable checkpoint fails when another
    DuckDB process still owns the file. Operators must also prevent service restart
    until publication completes; external source files and secrets are not copied.
    """
    _validate_runtime(provider, model, history_max_records)
    sources = {
        "catalog": _regular_file(catalog, "catalog"),
        "evidence": _regular_file(evidence_db, "evidence database"),
        "decision_trace": _regular_file(trace_db, "decision-trace database"),
        "query_history": _regular_file(history_db, "query-history database"),
    }
    if len(set(sources.values())) != len(sources):
        raise ValueError("runtime database paths must be distinct")

    supplied_target = Path(output)
    if supplied_target.is_symlink():
        raise FileExistsError("recovery destination already exists")
    target = supplied_target.resolve(strict=False)
    if target.exists() or target.name in {"", ".", ".."}:
        raise FileExistsError(f"recovery destination already exists or is invalid: {target}")
    if any(source == target or source in target.parents for source in sources.values()):
        raise ValueError("recovery destination must not contain a source database")

    # DuckDB's file lock is the process-level quiescence check for the serving
    # runtime: the service holds a read-only catalog connection for its lifetime.
    catalog_writer = duckdb.connect(str(sources["catalog"]), read_only=False)
    try:
        catalog_writer.execute("CHECKPOINT")
    finally:
        catalog_writer.close()
    catalog_fingerprint = _schema_fingerprint(sources["catalog"])

    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{target.name}.recovery-", dir=target.parent) as tmp:
        staged = Path(tmp) / "bundle"
        staged.mkdir()
        paths = {role: staged / name for role, name in ARTIFACTS.items()}
        _copy_durable(sources["catalog"], paths["catalog"])

        _sqlite_integrity(sources["evidence"])
        _sqlite_integrity(sources["decision_trace"])
        _sqlite_integrity(sources["query_history"])
        validate_source_database(
            sources["evidence"], journal_kind="evidence",
            schema_version=EVIDENCE_SCHEMA_VERSION,
        )
        validate_source_database(
            sources["decision_trace"], journal_kind="decision_trace",
            schema_version=TRACE_SCHEMA_VERSION,
        )
        history_preflight = verify_history_database(sources["query_history"])
        if not history_preflight.valid:
            raise ValueError("source query-history verification failed")
        evidence = SQLiteEvidenceStore(sources["evidence"])
        trace = SQLiteDecisionTraceStore(sources["decision_trace"])
        evidence_report = evidence.verify()
        if not evidence_report.valid:
            raise ValueError("source evidence journal verification failed")
        trace_report = trace.verify(evidence_store=evidence)
        if not trace_report.valid:
            raise ValueError("source decision-trace verification failed")
        evidence.backup(paths["evidence"])
        trace.backup(paths["decision_trace"])
        history = SQLiteQueryHistoryStore(sources["query_history"])
        try:
            history.backup(paths["query_history"])
        finally:
            history.close()

        copied_fingerprint = _schema_fingerprint(paths["catalog"])
        if copied_fingerprint != catalog_fingerprint:
            raise ValueError("catalog schema fingerprint changed during backup")

        counts = _verify_sqlite_stores(staged)
        artifact_manifest = {
            role: {"file": name, "bytes": paths[role].stat().st_size, "sha256": _sha256(paths[role])}
            for role, name in ARTIFACTS.items()
        }
        manifest: dict[str, object] = {
            "format_version": FORMAT_VERSION,
            "package_version": _package_version(),
            "created_at": datetime.now(UTC).isoformat(),
            "artifacts": artifact_manifest,
            "database_schema_versions": {
                "evidence": EVIDENCE_SCHEMA_VERSION,
                "decision_trace": TRACE_SCHEMA_VERSION,
                "query_history": HISTORY_SCHEMA_VERSION,
            },
            "catalog_schema_fingerprint": catalog_fingerprint,
            "journal_heads": {
                "evidence": {"records": counts[0], "head_sha256": counts[1]},
                "decision_trace": {
                "records": counts[2], "head_sha256": counts[3],
                "evidence_checked": counts[5],
            },
                "query_history": {"records": counts[4]},
            },
            "runtime": {
                "provider": provider,
                "model": model,
                "history_max_records": history_max_records,
            },
        }
        manifest["manifest_sha256"] = _manifest_digest(manifest)
        manifest_path = staged / MANIFEST_NAME
        manifest_path.write_bytes(_canonical(manifest) + b"\n")
        fsync_file(manifest_path)
        fsync_directory(staged)
        rename_directory_no_replace(staged, target)
        fsync_directory(target.parent)

    return RuntimeRecoveryReport(
        destination=str(target), format_version=FORMAT_VERSION,
        package_version=_package_version(), artifacts_verified=4,
        catalog_schema_fingerprint=catalog_fingerprint,
        evidence_records=counts[0], evidence_head_sha256=counts[1],
        decision_trace_records=counts[2], decision_trace_head_sha256=counts[3],
        traces_evidence_checked=trace_report.evidence_checked,
        query_history_records=counts[4], provider=provider, model=model,
        history_max_records=history_max_records,
    )


def _load_manifest(bundle: Path) -> dict[str, object]:
    manifest_path = bundle / MANIFEST_NAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("recovery manifest is missing or unsafe")
    parsed: object = json.loads(manifest_path.read_bytes())
    if not isinstance(parsed, dict):
        raise ValueError("recovery manifest must be an object")
    manifest = cast(dict[str, object], parsed)
    expected_keys = {
        "format_version", "package_version", "created_at", "artifacts",
        "database_schema_versions", "catalog_schema_fingerprint",
        "journal_heads", "runtime", "manifest_sha256",
    }
    if set(manifest) != expected_keys:
        raise ValueError("recovery manifest fields do not match the supported format")
    if type(manifest["format_version"]) is not int or manifest["format_version"] != FORMAT_VERSION:
        raise ValueError("unsupported recovery bundle format version")
    manifest_digest = manifest["manifest_sha256"]
    if not isinstance(manifest_digest, str) or manifest_digest != _manifest_digest(manifest):
        raise ValueError("recovery manifest digest mismatch")

    artifacts_value = manifest["artifacts"]
    if not isinstance(artifacts_value, dict):
        raise ValueError("recovery manifest artifact roles are invalid")
    artifacts = cast(dict[str, object], artifacts_value)
    if set(artifacts) != set(ARTIFACTS):
        raise ValueError("recovery manifest artifact roles are invalid")
    expected_names = {MANIFEST_NAME, *ARTIFACTS.values()}
    actual_names = {item.name for item in bundle.iterdir()}
    if actual_names != expected_names:
        raise ValueError("recovery bundle contains missing or unexpected files")
    for role, expected_name in ARTIFACTS.items():
        entry_value = artifacts[role]
        if not isinstance(entry_value, dict):
            raise ValueError("invalid recovery artifact metadata")
        entry = cast(dict[str, object], entry_value)
        if set(entry) != {"file", "bytes", "sha256"}:
            raise ValueError("invalid recovery artifact metadata")
        if entry["file"] != expected_name:
            raise ValueError("unsafe recovery artifact filename")
        size = entry["bytes"]
        digest = entry["sha256"]
        if type(size) is not int or not isinstance(digest, str):
            raise ValueError("invalid recovery artifact size or digest")
        path = bundle / expected_name
        if path.is_symlink() or not path.is_file():
            raise ValueError("recovery artifact is missing or unsafe")
        if size != path.stat().st_size or digest != _sha256(path):
            raise ValueError(f"recovery artifact digest or size mismatch: {role}")

    expected_schemas = {
        "evidence": EVIDENCE_SCHEMA_VERSION,
        "decision_trace": TRACE_SCHEMA_VERSION,
        "query_history": HISTORY_SCHEMA_VERSION,
    }
    if manifest["database_schema_versions"] != expected_schemas:
        raise ValueError("unsupported recovery database schema version")
    fingerprint = manifest["catalog_schema_fingerprint"]
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        raise ValueError("invalid catalog schema fingerprint in manifest")

    heads_value = manifest["journal_heads"]
    if not isinstance(heads_value, dict):
        raise ValueError("invalid journal-head manifest")
    heads = cast(dict[str, object], heads_value)
    if set(heads) != {"evidence", "decision_trace", "query_history"}:
        raise ValueError("invalid journal-head manifest")
    evidence_value = heads["evidence"]
    trace_value = heads["decision_trace"]
    history_value = heads["query_history"]
    if not all(isinstance(item, dict) for item in (evidence_value, trace_value, history_value)):
        raise ValueError("invalid journal-head manifest")
    evidence_head = cast(dict[str, object], evidence_value)
    trace_head = cast(dict[str, object], trace_value)
    history_head = cast(dict[str, object], history_value)
    if set(evidence_head) != {"records", "head_sha256"}:
        raise ValueError("invalid evidence journal-head manifest")
    if set(trace_head) != {"records", "head_sha256", "evidence_checked"}:
        raise ValueError("invalid decision-trace journal-head manifest")
    if set(history_head) != {"records"}:
        raise ValueError("invalid query-history record manifest")
    for item in (evidence_head, trace_head, history_head):
        if type(item["records"]) is not int or item["records"] < 0:
            raise ValueError("invalid journal record count in manifest")
    for item in (evidence_head, trace_head):
        digest = item["head_sha256"]
        if not isinstance(digest, str) or len(digest) != 64 or any(
            char not in "0123456789abcdef" for char in digest
        ):
            raise ValueError("invalid journal head digest in manifest")
    if (
        type(trace_head["evidence_checked"]) is not int
        or trace_head["evidence_checked"] < 0
    ):
        raise ValueError("invalid trace/evidence reference count in manifest")

    runtime_value = manifest["runtime"]
    if not isinstance(runtime_value, dict):
        raise ValueError("invalid sanitized runtime configuration")
    runtime = cast(dict[str, object], runtime_value)
    if set(runtime) != {"provider", "model", "history_max_records"}:
        raise ValueError("invalid sanitized runtime configuration")
    provider = runtime["provider"]
    model = runtime["model"]
    history_max_records = runtime["history_max_records"]
    if not isinstance(provider, str) or not isinstance(model, str):
        raise ValueError("invalid provider/model configuration in manifest")
    if history_max_records is not None and type(history_max_records) is not int:
        raise ValueError("invalid history retention setting in manifest")
    _validate_runtime(provider, model, cast(int | None, history_max_records))

    if not isinstance(manifest["package_version"], str) or not isinstance(manifest["created_at"], str):
        raise ValueError("invalid package version or creation timestamp in manifest")
    return manifest


def restore_runtime(
    bundle_path: str | Path, output: str | Path
) -> RuntimeRecoveryReport:
    """Verify a bundle and atomically publish all four databases into a new directory."""
    supplied_bundle = Path(bundle_path)
    if supplied_bundle.is_symlink() or not supplied_bundle.is_dir():
        raise ValueError("recovery bundle must be a regular directory")
    bundle = supplied_bundle.resolve(strict=True)
    manifest = _load_manifest(bundle)

    supplied_target = Path(output)
    if supplied_target.is_symlink():
        raise FileExistsError("restore destination already exists")
    target = supplied_target.resolve(strict=False)
    if target.exists() or target.name in {"", ".", ".."}:
        raise FileExistsError(f"restore destination already exists or is invalid: {target}")
    if target == bundle or bundle in target.parents:
        raise ValueError("restore destination must be outside the source recovery bundle")

    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{target.name}.restore-", dir=target.parent) as tmp:
        staged = Path(tmp) / "restored"
        staged.mkdir()
        for name in ARTIFACTS.values():
            _copy_durable(bundle / name, staged / name)
        fingerprint = _schema_fingerprint(staged / ARTIFACTS["catalog"])
        if fingerprint != manifest["catalog_schema_fingerprint"]:
            raise ValueError("restored catalog schema fingerprint mismatch")
        counts = _verify_sqlite_stores(staged)
        expected_heads = manifest["journal_heads"]
        if (
            counts[0] != expected_heads["evidence"]["records"]
            or counts[1] != expected_heads["evidence"]["head_sha256"]
            or counts[2] != expected_heads["decision_trace"]["records"]
            or counts[3] != expected_heads["decision_trace"]["head_sha256"]
            or counts[4] != expected_heads["query_history"]["records"]
            or counts[5] != expected_heads["decision_trace"]["evidence_checked"]
        ):
            raise ValueError("restored journal heads or record counts differ from the manifest")
        for path in staged.iterdir():
            fsync_file(path)
        fsync_directory(staged)
        rename_directory_no_replace(staged, target)
        fsync_directory(target.parent)

    runtime = cast(dict[str, object], manifest["runtime"])
    return RuntimeRecoveryReport(
        destination=str(target), format_version=FORMAT_VERSION,
        package_version=cast(str, manifest["package_version"]), artifacts_verified=4,
        catalog_schema_fingerprint=fingerprint,
        evidence_records=counts[0], evidence_head_sha256=counts[1],
        decision_trace_records=counts[2], decision_trace_head_sha256=counts[3],
        traces_evidence_checked=counts[5],
        query_history_records=counts[4], provider=cast(str, runtime["provider"]),
        model=cast(str, runtime["model"]),
        history_max_records=cast(int | None, runtime["history_max_records"]),
    )
