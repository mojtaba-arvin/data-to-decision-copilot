"""Read-only operator diagnostics for local storage capacity."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Mapping


def inspect_storage(
    paths: Mapping[str, str | Path],
    *,
    warning_free_percent: float = 15.0,
    critical_free_percent: float = 5.0,
) -> dict[str, object]:
    """Report filesystem capacity for configured data paths without modifying them."""
    if not 0 <= critical_free_percent < warning_free_percent <= 100:
        raise ValueError(
            "storage thresholds must satisfy 0 <= critical < warning <= 100"
        )
    if not paths:
        raise ValueError("at least one storage path must be provided")

    filesystems: list[dict[str, object]] = []
    statuses: list[str] = []
    for name, raw_path in paths.items():
        path = Path(raw_path).expanduser().resolve()
        probe = path if path.is_dir() else path.parent
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        if not probe.is_dir():
            probe = probe.parent
        try:
            usage = shutil.disk_usage(probe)
        except OSError:
            status = "unavailable"
            item: dict[str, object] = {
                "name": name,
                "path": str(path),
                "filesystem_path": str(probe),
                "status": status,
            }
        else:
            free_percent = (usage.free / usage.total * 100) if usage.total else 0.0
            if free_percent <= critical_free_percent:
                status = "critical"
            elif free_percent <= warning_free_percent:
                status = "warning"
            else:
                status = "ok"
            item = {
                "name": name,
                "path": str(path),
                "filesystem_path": str(probe),
                "total_bytes": usage.total,
                "free_bytes": usage.free,
                "free_percent": round(free_percent, 2),
                "status": status,
            }
        statuses.append(status)
        filesystems.append(item)

    overall = (
        "critical" if "critical" in statuses
        else "unavailable" if "unavailable" in statuses
        else "warning" if "warning" in statuses
        else "ok"
    )
    return {
        "status": overall,
        "thresholds": {
            "warning_free_percent": warning_free_percent,
            "critical_free_percent": critical_free_percent,
        },
        "filesystems": filesystems,
    }
