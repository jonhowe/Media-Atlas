from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _csv_env(name: str, default: list[str]) -> list[str]:
    value = os.getenv(name)
    if not value:
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class ScannerConfig:
    ffprobe_path: str
    concurrency: int
    timeout_seconds: int
    mark_missing_files: bool


@dataclass(frozen=True)
class TranscoderConfig:
    ffmpeg_path: str
    concurrency: int
    timeout_seconds: int
    staging_dir: Path
    duration_tolerance_seconds: float
    duration_tolerance_percent: float


@dataclass(frozen=True)
class AppConfig:
    host: str
    port: int
    data_dir: Path
    reports_dir: Path
    logs_dir: Path
    database_path: Path
    allow_lan: bool
    directory_browser_enabled: bool
    allowed_browse_roots: list[Path]
    scanner: ScannerConfig
    transcoder: TranscoderConfig


DEFAULT_EXTENSIONS = [
    ".mkv",
    ".mp4",
    ".m4v",
    ".avi",
    ".mov",
    ".wmv",
    ".mpg",
    ".mpeg",
    ".ts",
    ".m2ts",
    ".flv",
    ".webm",
    ".ogm",
    ".iso",
    ".vob",
]

DEFAULT_EXCLUDES = [
    "*.part",
    "*.partial",
    "*.tmp",
    "*.download",
    "*/sample/*",
    "*/samples/*",
    "@eaDir/*",
    ".DS_Store",
]


def load_config() -> AppConfig:
    repo_root = Path(__file__).resolve().parents[2]
    base_dir = Path(os.getenv("MEDIA_ATLAS_BASE_DIR", repo_root)).resolve()
    data_dir = Path(os.getenv("MEDIA_ATLAS_DATA_DIR", base_dir / "data")).resolve()
    reports_dir = Path(os.getenv("MEDIA_ATLAS_REPORTS_DIR", base_dir / "reports")).resolve()
    logs_dir = Path(os.getenv("MEDIA_ATLAS_LOGS_DIR", base_dir / "logs")).resolve()
    staging_dir = Path(
        os.getenv("MEDIA_ATLAS_TRANSCODE_STAGING_DIR", base_dir / "transcode-staging")
    ).resolve()

    for directory in (data_dir, reports_dir, logs_dir, staging_dir):
        directory.mkdir(parents=True, exist_ok=True)

    default_browse_roots = [
        Path.home(),
        Path("/mnt"),
        Path("/media"),
        Path("/Volumes"),
        base_dir,
    ]
    allowed_browse_roots = [
        Path(item).expanduser().resolve()
        for item in _csv_env(
            "MEDIA_ATLAS_ALLOWED_BROWSE_ROOTS",
            [str(path) for path in default_browse_roots if path.exists()],
        )
    ]

    host = os.getenv("MEDIA_ATLAS_HOST", "127.0.0.1")
    return AppConfig(
        host=host,
        port=_int_env("MEDIA_ATLAS_PORT", 8000),
        data_dir=data_dir,
        reports_dir=reports_dir,
        logs_dir=logs_dir,
        database_path=Path(
            os.getenv("MEDIA_ATLAS_DATABASE_PATH", data_dir / "media_inventory.sqlite")
        ).resolve(),
        allow_lan=_bool_env("MEDIA_ATLAS_ALLOW_LAN", host not in {"127.0.0.1", "localhost"}),
        directory_browser_enabled=_bool_env("MEDIA_ATLAS_DIRECTORY_BROWSER_ENABLED", True),
        allowed_browse_roots=allowed_browse_roots,
        scanner=ScannerConfig(
            ffprobe_path=os.getenv("MEDIA_ATLAS_FFPROBE_PATH", "ffprobe"),
            concurrency=max(1, _int_env("MEDIA_ATLAS_SCAN_CONCURRENCY", 2)),
            timeout_seconds=max(5, _int_env("MEDIA_ATLAS_FFPROBE_TIMEOUT_SECONDS", 60)),
            mark_missing_files=_bool_env("MEDIA_ATLAS_MARK_MISSING_FILES", True),
        ),
        transcoder=TranscoderConfig(
            ffmpeg_path=os.getenv("MEDIA_ATLAS_FFMPEG_PATH", "ffmpeg"),
            concurrency=1,
            timeout_seconds=max(30, _int_env("MEDIA_ATLAS_FFMPEG_TIMEOUT_SECONDS", 0)),
            staging_dir=staging_dir,
            duration_tolerance_seconds=float(
                os.getenv("MEDIA_ATLAS_TRANSCODE_DURATION_TOLERANCE_SECONDS", "3")
            ),
            duration_tolerance_percent=float(
                os.getenv("MEDIA_ATLAS_TRANSCODE_DURATION_TOLERANCE_PERCENT", "0.02")
            ),
        ),
    )


CONFIG = load_config()
