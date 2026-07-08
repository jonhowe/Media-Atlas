from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from . import db
from .config import CONFIG
from .security import auth_warnings, redacted_config
from .services.plex import status_summary as plex_status_summary


def live_status() -> dict[str, Any]:
    return {"status": "alive"}


def version_status() -> dict[str, Any]:
    return {
        "version": CONFIG.version.version,
        "git_sha": CONFIG.version.git_sha,
        "build_date": CONFIG.version.build_date,
        "image_tag": CONFIG.version.image_tag,
    }


def readiness_status() -> dict[str, Any]:
    checks: dict[str, Any] = {
        "database": _database_check(),
        "migrations": db.migration_status(),
        "paths": _path_checks(),
        "disk": _disk_checks(),
        "tools": _tool_checks(),
        "plex": _plex_check(),
        "config_warnings": CONFIG.config_warnings + auth_warnings(),
        "jobs": job_state_counts(),
    }
    ok = (
        checks["database"]["ok"]
        and checks["migrations"]["ok"]
        and all(item["writable"] for item in checks["paths"].values())
        and all(item["ok"] for item in checks["disk"].values())
        and checks["tools"]["ffprobe"]["available"]
        and checks["tools"]["ffmpeg"]["available"]
        and not CONFIG.config_warnings
        and not auth_warnings()
    )
    return {"status": "ok" if ok else "degraded", "ok": ok, **checks}


def admin_status() -> dict[str, Any]:
    readiness = readiness_status()
    return {
        "version": version_status(),
        "readiness": readiness,
        "auth": redacted_config(),
        "runtime_config": {
            "host": CONFIG.host,
            "port": CONFIG.port,
            "allow_lan": CONFIG.allow_lan,
            "auth": {
                "mode": CONFIG.auth.mode,
            },
            "operations": {
                "acknowledge_auth_disabled_lan": CONFIG.operations.acknowledge_auth_disabled_lan,
                "fail_unsafe_bind": CONFIG.operations.fail_unsafe_bind,
                "allowed_origins": CONFIG.operations.allowed_origins,
            },
        },
        "storage": {
            "data_dir": _disk_payload(CONFIG.data_dir),
            "reports_dir": _disk_payload(CONFIG.reports_dir),
            "logs_dir": _disk_payload(CONFIG.logs_dir),
            "transcode_staging_dir": _disk_payload(CONFIG.transcoder.staging_dir),
            "transcode_backup_dir": _disk_payload(CONFIG.transcoder.backup_dir),
        },
        "recent_failures": {
            "scans": db.query_all(
                "SELECT * FROM scan_jobs WHERE status IN ('failed', 'interrupted') ORDER BY id DESC LIMIT 5"
            ),
            "transcodes": db.query_all(
                "SELECT * FROM transcode_runs WHERE status IN ('failed', 'interrupted') ORDER BY id DESC LIMIT 5"
            ),
            "plex_syncs": db.query_all(
                "SELECT * FROM plex_sync_jobs WHERE status IN ('failed', 'interrupted') ORDER BY id DESC LIMIT 5"
            ),
        },
        "retention": {
            "log_retention_days": CONFIG.operations.log_retention_days,
            "staged_output_retention_days": CONFIG.operations.staged_output_retention_days,
        },
    }


def diagnostics_status() -> dict[str, Any]:
    status = admin_status()
    return {
        "generated_at": db.utc_now(),
        "version": status["version"],
        "runtime_config": status["runtime_config"],
        "auth": status["auth"],
        "readiness": status["readiness"],
        "storage": status["storage"],
        "recent_failures": status["recent_failures"],
        "retention": status["retention"],
    }


def metrics_status() -> dict[str, Any]:
    media = db.query_one(
        """
        SELECT COUNT(*) AS files,
               COALESCE(SUM(size_bytes), 0) AS size_bytes,
               COALESCE(SUM(duration_seconds), 0) AS duration_seconds
        FROM files
        """
    ) or {"files": 0, "size_bytes": 0, "duration_seconds": 0}
    return {
        "media": media,
        "jobs": job_state_counts(),
        "plex": plex_status_summary(),
        "migrations": db.migration_status(),
    }


def job_state_counts() -> dict[str, Any]:
    return {
        "scans": _status_counts("scan_jobs"),
        "transcode_runs": _status_counts("transcode_runs"),
        "transcode_items": _status_counts("transcode_run_items"),
        "plex_syncs": _status_counts("plex_sync_jobs"),
    }


def _database_check() -> dict[str, Any]:
    try:
        row = db.query_one("SELECT 1 AS ok")
        return {"ok": bool(row and row["ok"] == 1), "path": str(CONFIG.database_path)}
    except Exception as exc:
        return {"ok": False, "path": str(CONFIG.database_path), "error": str(exc)}


def _path_checks() -> dict[str, dict[str, Any]]:
    paths = {
        "data_dir": CONFIG.data_dir,
        "reports_dir": CONFIG.reports_dir,
        "logs_dir": CONFIG.logs_dir,
        "transcode_staging_dir": CONFIG.transcoder.staging_dir,
        "transcode_backup_dir": CONFIG.transcoder.backup_dir,
    }
    return {key: _writable_path_check(path) for key, path in paths.items()}


def _writable_path_check(path: Path) -> dict[str, Any]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".media-atlas-write-", dir=path, delete=True):
            pass
        return {"path": str(path), "writable": True}
    except Exception as exc:
        return {"path": str(path), "writable": False, "error": str(exc)}


def _disk_checks() -> dict[str, dict[str, Any]]:
    return {
        "data_dir": _disk_payload(CONFIG.data_dir),
        "transcode_staging_dir": _disk_payload(CONFIG.transcoder.staging_dir),
        "transcode_backup_dir": _disk_payload(CONFIG.transcoder.backup_dir),
    }


def _disk_payload(path: Path) -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(path)
        ok = usage.free >= CONFIG.operations.readiness_min_free_bytes
        return {
            "path": str(path),
            "ok": ok,
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "min_free_bytes": CONFIG.operations.readiness_min_free_bytes,
        }
    except Exception as exc:
        return {"path": str(path), "ok": False, "error": str(exc)}


def _tool_checks() -> dict[str, dict[str, Any]]:
    return {
        "ffprobe": _tool_payload(CONFIG.scanner.ffprobe_path),
        "ffmpeg": _tool_payload(CONFIG.transcoder.ffmpeg_path),
    }


def _tool_payload(command: str) -> dict[str, Any]:
    resolved = shutil.which(command) if not os.path.isabs(command) else command
    if not resolved:
        return {"command": command, "available": False, "version": None}
    version = None
    try:
        result = subprocess.run(
            [resolved, "-version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
        version = (result.stdout or result.stderr).splitlines()[0] if (result.stdout or result.stderr) else None
    except Exception as exc:
        version = f"version check failed: {exc}"
    return {"command": command, "path": resolved, "available": True, "version": version}


def _plex_check() -> dict[str, Any]:
    try:
        return plex_status_summary()
    except Exception as exc:
        return {"configured": False, "enabled": False, "error": str(exc)}


def _status_counts(table: str) -> dict[str, int]:
    allowed = {"scan_jobs", "transcode_runs", "transcode_run_items", "plex_sync_jobs"}
    if table not in allowed:
        return {}
    rows = db.query_all(f"SELECT status, COUNT(*) AS count FROM {table} GROUP BY status")
    return {str(row["status"]): int(row["count"]) for row in rows}
