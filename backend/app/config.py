from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


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


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
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
    min_free_bytes: int


@dataclass(frozen=True)
class AuthConfig:
    mode: Literal["disabled", "single_admin", "reverse_proxy_trusted"]
    admin_username: str
    admin_password: str
    admin_password_hash: str
    session_secret: str
    session_cookie_name: str
    session_ttl_seconds: int
    cookie_secure: bool
    trusted_user_header: str


@dataclass(frozen=True)
class OperationsConfig:
    allowed_origins: list[str]
    acknowledge_auth_disabled_lan: bool
    fail_unsafe_bind: bool
    readiness_min_free_bytes: int
    log_retention_days: int
    staged_output_retention_days: int


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
    config_warnings: list[str]
    auth: AuthConfig
    operations: OperationsConfig
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
    auth_mode = os.getenv("MEDIA_ATLAS_AUTH_MODE", "disabled").strip().lower()
    if auth_mode not in {"disabled", "single_admin", "reverse_proxy_trusted"}:
        auth_mode = "disabled"
    allowed_origins = _csv_env(
        "MEDIA_ATLAS_ALLOWED_ORIGINS",
        [
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:8000",
            "http://localhost:8000",
        ],
    )
    acknowledge_auth_disabled_lan = _bool_env("MEDIA_ATLAS_ACKNOWLEDGE_AUTH_DISABLED_LAN", False)
    fail_unsafe_bind = _bool_env("MEDIA_ATLAS_FAIL_UNSAFE_BIND", False)
    config_warnings: list[str] = []
    if host in {"0.0.0.0", "::"} and auth_mode == "disabled" and not acknowledge_auth_disabled_lan:
        message = (
            "Media Atlas is bound to all interfaces with auth disabled. "
            "Set MEDIA_ATLAS_AUTH_MODE=single_admin or explicitly set "
            "MEDIA_ATLAS_ACKNOWLEDGE_AUTH_DISABLED_LAN=true for trusted LAN/VPN use."
        )
        config_warnings.append(message)
        if fail_unsafe_bind:
            raise RuntimeError(message)

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
        config_warnings=config_warnings,
        auth=AuthConfig(
            mode=auth_mode,  # type: ignore[arg-type]
            admin_username=os.getenv("MEDIA_ATLAS_ADMIN_USERNAME", "admin"),
            admin_password=os.getenv("MEDIA_ATLAS_ADMIN_PASSWORD", ""),
            admin_password_hash=os.getenv("MEDIA_ATLAS_ADMIN_PASSWORD_HASH", ""),
            session_secret=os.getenv("MEDIA_ATLAS_SESSION_SECRET", ""),
            session_cookie_name=os.getenv("MEDIA_ATLAS_SESSION_COOKIE_NAME", "media_atlas_session"),
            session_ttl_seconds=max(300, _int_env("MEDIA_ATLAS_SESSION_TTL_SECONDS", 60 * 60 * 12)),
            cookie_secure=_bool_env("MEDIA_ATLAS_SESSION_COOKIE_SECURE", False),
            trusted_user_header=os.getenv("MEDIA_ATLAS_TRUSTED_USER_HEADER", "X-Forwarded-User"),
        ),
        operations=OperationsConfig(
            allowed_origins=allowed_origins,
            acknowledge_auth_disabled_lan=acknowledge_auth_disabled_lan,
            fail_unsafe_bind=fail_unsafe_bind,
            readiness_min_free_bytes=max(
                0, _int_env("MEDIA_ATLAS_READINESS_MIN_FREE_BYTES", 256 * 1024 * 1024)
            ),
            log_retention_days=max(0, _int_env("MEDIA_ATLAS_LOG_RETENTION_DAYS", 30)),
            staged_output_retention_days=max(
                0, _int_env("MEDIA_ATLAS_STAGED_OUTPUT_RETENTION_DAYS", 0)
            ),
        ),
        scanner=ScannerConfig(
            ffprobe_path=os.getenv("MEDIA_ATLAS_FFPROBE_PATH", "ffprobe"),
            concurrency=max(1, _int_env("MEDIA_ATLAS_SCAN_CONCURRENCY", 2)),
            timeout_seconds=max(5, _int_env("MEDIA_ATLAS_FFPROBE_TIMEOUT_SECONDS", 60)),
            mark_missing_files=_bool_env("MEDIA_ATLAS_MARK_MISSING_FILES", True),
        ),
        transcoder=TranscoderConfig(
            ffmpeg_path=os.getenv("MEDIA_ATLAS_FFMPEG_PATH", "ffmpeg"),
            concurrency=1,
            timeout_seconds=max(0, _int_env("MEDIA_ATLAS_FFMPEG_TIMEOUT_SECONDS", 0)),
            staging_dir=staging_dir,
            duration_tolerance_seconds=_float_env("MEDIA_ATLAS_TRANSCODE_DURATION_TOLERANCE_SECONDS", 3),
            duration_tolerance_percent=_float_env("MEDIA_ATLAS_TRANSCODE_DURATION_TOLERANCE_PERCENT", 0.02),
            min_free_bytes=max(
                0, _int_env("MEDIA_ATLAS_TRANSCODE_MIN_FREE_BYTES", 1024 * 1024 * 1024)
            ),
        ),
    )


CONFIG = load_config()
