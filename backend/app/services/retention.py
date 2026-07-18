from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from ..config import CONFIG
from ..logging_config import application_log_path

LOGGER = logging.getLogger("media_atlas.retention")


def apply_retention() -> dict[str, Any]:
    return {
        "logs_removed": _remove_old_files(
            CONFIG.logs_dir,
            CONFIG.operations.log_retention_days,
            excluded_paths={application_log_path(CONFIG.logs_dir)},
        ),
        "quarantined_outputs_removed": _remove_old_files(
            CONFIG.transcoder.staging_dir / ".quarantine",
            CONFIG.operations.staged_output_retention_days,
        ),
    }


def _remove_old_files(root: Path, retention_days: int, excluded_paths: set[Path] | None = None) -> int:
    if retention_days <= 0 or not root.exists():
        return 0
    excluded = {path.resolve() for path in (excluded_paths or set())}
    cutoff = time.time() - (retention_days * 24 * 60 * 60)
    removed = 0
    for path in sorted(root.rglob("*"), reverse=True):
        try:
            if path.resolve() in excluded:
                continue
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()
        except OSError as exc:
            LOGGER.warning("Retention cleanup skipped %s: %s", path, exc)
    return removed
