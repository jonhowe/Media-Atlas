from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from ..config import CONFIG

LOGGER = logging.getLogger("media_atlas.retention")


def apply_retention() -> dict[str, Any]:
    return {
        "logs_removed": _remove_old_files(CONFIG.logs_dir, CONFIG.operations.log_retention_days),
        "quarantined_outputs_removed": _remove_old_files(
            CONFIG.transcoder.staging_dir / ".quarantine",
            CONFIG.operations.staged_output_retention_days,
        ),
    }


def _remove_old_files(root: Path, retention_days: int) -> int:
    if retention_days <= 0 or not root.exists():
        return 0
    cutoff = time.time() - (retention_days * 24 * 60 * 60)
    removed = 0
    for path in sorted(root.rglob("*"), reverse=True):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()
        except OSError as exc:
            LOGGER.warning("Retention cleanup skipped %s: %s", path, exc)
    return removed
