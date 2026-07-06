from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import CONFIG, DEFAULT_EXCLUDES, DEFAULT_EXTENSIONS

_SCHEMA_LOCK = threading.Lock()
_MIGRATION_STATUS: dict[str, Any] = {
    "ok": False,
    "applied": [],
    "pending": [],
    "error": "Migrations have not run yet.",
    "last_run_at": None,
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


@contextmanager
def connect() -> Iterable[sqlite3.Connection]:
    connection = sqlite3.connect(CONFIG.database_path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def execute(sql: str, params: tuple[Any, ...] = ()) -> int:
    with connect() as connection:
        cursor = connection.execute(sql, params)
        return cursor.lastrowid


def executemany(sql: str, params: list[tuple[Any, ...]]) -> None:
    with connect() as connection:
        connection.executemany(sql, params)


def query_one(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    with connect() as connection:
        return row_to_dict(connection.execute(sql, params).fetchone())


def query_all(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with connect() as connection:
        return [row_to_dict(row) for row in connection.execute(sql, params).fetchall()]  # type: ignore[list-item]


def init_db() -> None:
    CONFIG.database_path.parent.mkdir(parents=True, exist_ok=True)
    with _SCHEMA_LOCK:
        run_migrations()
        seed_transcode_profiles()
        mark_running_transcodes_interrupted()


def run_migrations() -> None:
    global _MIGRATION_STATUS
    now = utc_now()
    try:
        with connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                  version TEXT PRIMARY KEY,
                  applied_at TEXT NOT NULL
                )
                """
            )
            applied = {
                row["version"]
                for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
            }
            newly_applied: list[str] = []
            for version, script in MIGRATIONS:
                if version in applied:
                    continue
                connection.executescript(script)
                connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                    (version, utc_now()),
                )
                newly_applied.append(version)
                applied.add(version)
            pending = [version for version, _ in MIGRATIONS if version not in applied]
        _MIGRATION_STATUS = {
            "ok": True,
            "applied": sorted(applied),
            "newly_applied": newly_applied,
            "pending": pending,
            "error": None,
            "last_run_at": now,
        }
    except Exception as exc:
        _MIGRATION_STATUS = {
            "ok": False,
            "applied": [],
            "pending": [version for version, _ in MIGRATIONS],
            "error": str(exc),
            "last_run_at": now,
        }
        raise


def migration_status() -> dict[str, Any]:
    return dict(_MIGRATION_STATUS)


def create_database_backup() -> Path:
    backup_dir = CONFIG.data_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"media_inventory-{timestamp}.sqlite"
    with sqlite3.connect(CONFIG.database_path) as source:
        with sqlite3.connect(backup_path) as target:
            source.backup(target)
    return backup_path


def seed_transcode_profiles() -> None:
    now = utc_now()
    profiles = [
        (
            "Remux to MKV",
            "Copy all streams into an MKV container without re-encoding.",
            "mkv",
            "copy",
            "copy",
            "copy",
            "remux_mkv",
        ),
        (
            "HEVC Archive Balanced",
            "Encode video with libx265 CRF 20 while copying audio and subtitles.",
            "mkv",
            "libx265",
            "copy",
            "copy",
            "hevc_archive",
        ),
        (
            "HEVC Archive Fast",
            "Encode video with libx265 CRF 21 preset fast while copying audio and subtitles.",
            "mkv",
            "libx265",
            "copy",
            "copy",
            "hevc_archive_fast",
        ),
        (
            "HEVC Archive Faster",
            "Encode video with libx265 CRF 22 preset faster while copying audio and subtitles.",
            "mkv",
            "libx265",
            "copy",
            "copy",
            "hevc_archive_faster",
        ),
        (
            "HEVC Quick Sync",
            "Encode video with Intel Quick Sync hevc_qsv. Requires host/container hardware support.",
            "mkv",
            "hevc_qsv",
            "copy",
            "copy",
            "hevc_qsv",
        ),
        (
            "HEVC NVENC",
            "Encode video with NVIDIA hevc_nvenc. Requires NVIDIA drivers and container runtime support.",
            "mkv",
            "hevc_nvenc",
            "copy",
            "copy",
            "hevc_nvenc",
        ),
        (
            "HEVC VAAPI",
            "Encode video with VAAPI hevc_vaapi. Requires /dev/dri access in the container.",
            "mkv",
            "hevc_vaapi",
            "copy",
            "copy",
            "hevc_vaapi",
        ),
        (
            "H.264 Compatibility",
            "Encode H.264 video and AAC audio into an MP4 compatibility target.",
            "mp4",
            "libx264",
            "aac",
            "mov_text",
            "h264_compat",
        ),
        (
            "Manual Review Only",
            "Do not generate a command; use for complex files that need human review.",
            "none",
            "none",
            "none",
            "none",
            "manual_review",
        ),
    ]
    with connect() as connection:
        for profile in profiles:
            connection.execute(
                """
                INSERT OR IGNORE INTO transcode_profiles (
                    name, description, container, video_codec, audio_policy,
                    subtitle_policy, command_template, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (*profile, now, now),
            )


def mark_running_transcodes_interrupted() -> None:
    now = utc_now()
    with connect() as connection:
        connection.execute(
            """
            UPDATE transcode_run_items
            SET status = 'interrupted',
                finished_at = COALESCE(finished_at, ?),
                verification_status = 'not_run',
                verification_message = 'Backend restarted while this item was active.'
            WHERE status = 'running'
            """,
            (now,),
        )
        connection.execute(
            """
            UPDATE transcode_runs
            SET status = 'interrupted',
                finished_at = COALESCE(finished_at, ?),
                message = 'Backend restarted while this run was active.'
            WHERE status = 'running'
            """,
            (now,),
        )


SCHEMA = """
CREATE TABLE IF NOT EXISTS media_roots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  path TEXT NOT NULL UNIQUE,
  enabled INTEGER NOT NULL DEFAULT 1,
  include_extensions_json TEXT NOT NULL,
  exclude_patterns_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_scanned_at TEXT
);

CREATE TABLE IF NOT EXISTS files (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  root_id INTEGER NOT NULL REFERENCES media_roots(id),
  path TEXT NOT NULL UNIQUE,
  directory TEXT NOT NULL,
  filename TEXT NOT NULL,
  extension TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  modified_time_ns INTEGER NOT NULL,
  created_time_ns INTEGER,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  last_scanned_at TEXT,
  is_missing INTEGER NOT NULL DEFAULT 0,
  missing_since TEXT,
  format_name TEXT,
  format_long_name TEXT,
  container TEXT,
  duration_seconds REAL,
  overall_bitrate INTEGER,
  primary_video_codec TEXT,
  primary_video_codec_long TEXT,
  primary_video_profile TEXT,
  width INTEGER,
  height INTEGER,
  resolution_bucket TEXT,
  frame_rate REAL,
  video_bitrate INTEGER,
  pixel_format TEXT,
  bit_depth INTEGER,
  color_space TEXT,
  color_transfer TEXT,
  color_primaries TEXT,
  hdr_format TEXT,
  is_hdr INTEGER NOT NULL DEFAULT 0,
  is_interlaced INTEGER,
  primary_audio_codec TEXT,
  primary_audio_codec_long TEXT,
  primary_audio_channels INTEGER,
  primary_audio_channel_layout TEXT,
  primary_audio_language TEXT,
  audio_stream_count INTEGER NOT NULL DEFAULT 0,
  subtitle_stream_count INTEGER NOT NULL DEFAULT 0,
  video_stream_count INTEGER NOT NULL DEFAULT 0,
  subtitle_codecs TEXT,
  subtitle_languages TEXT,
  has_forced_subtitles INTEGER NOT NULL DEFAULT 0,
  has_image_subtitles INTEGER NOT NULL DEFAULT 0,
  audio_summary TEXT,
  size_per_hour_gb REAL,
  bitrate_mbps REAL,
  recommendation_category TEXT,
  recommendation_summary TEXT,
  recommendation_reasons_json TEXT,
  recommendation_warnings_json TEXT,
  raw_probe_json TEXT,
  probe_error TEXT,
  probe_exit_code INTEGER,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_files_root_id ON files(root_id);
CREATE INDEX IF NOT EXISTS idx_files_path ON files(path);
CREATE INDEX IF NOT EXISTS idx_files_extension ON files(extension);
CREATE INDEX IF NOT EXISTS idx_files_size ON files(size_bytes);
CREATE INDEX IF NOT EXISTS idx_files_video_codec ON files(primary_video_codec);
CREATE INDEX IF NOT EXISTS idx_files_audio_codec ON files(primary_audio_codec);
CREATE INDEX IF NOT EXISTS idx_files_container ON files(container);
CREATE INDEX IF NOT EXISTS idx_files_resolution ON files(resolution_bucket);
CREATE INDEX IF NOT EXISTS idx_files_recommendation ON files(recommendation_category);
CREATE INDEX IF NOT EXISTS idx_files_missing ON files(is_missing);

CREATE TABLE IF NOT EXISTS streams (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  stream_index INTEGER NOT NULL,
  stream_type TEXT NOT NULL,
  codec_name TEXT,
  codec_long_name TEXT,
  profile TEXT,
  language TEXT,
  title TEXT,
  disposition_default INTEGER,
  disposition_forced INTEGER,
  width INTEGER,
  height INTEGER,
  frame_rate REAL,
  channels INTEGER,
  channel_layout TEXT,
  sample_rate INTEGER,
  bit_rate INTEGER,
  bits_per_raw_sample INTEGER,
  pixel_format TEXT,
  color_space TEXT,
  color_transfer TEXT,
  color_primaries TEXT,
  duration_seconds REAL,
  raw_stream_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_streams_file_id ON streams(file_id);
CREATE INDEX IF NOT EXISTS idx_streams_type ON streams(stream_type);
CREATE INDEX IF NOT EXISTS idx_streams_codec ON streams(codec_name);

CREATE TABLE IF NOT EXISTS chapters (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  chapter_index INTEGER NOT NULL,
  start_seconds REAL,
  end_seconds REAL,
  title TEXT,
  raw_chapter_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scan_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  status TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  created_at TEXT NOT NULL,
  requested_by TEXT,
  total_files_discovered INTEGER NOT NULL DEFAULT 0,
  files_skipped INTEGER NOT NULL DEFAULT 0,
  files_probed INTEGER NOT NULL DEFAULT 0,
  files_failed INTEGER NOT NULL DEFAULT 0,
  current_path TEXT,
  message TEXT,
  cancel_requested INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_scan_jobs_status ON scan_jobs(status);

CREATE TABLE IF NOT EXISTS scan_errors (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scan_job_id INTEGER REFERENCES scan_jobs(id),
  path TEXT NOT NULL,
  error_type TEXT NOT NULL,
  error_message TEXT NOT NULL,
  ffprobe_exit_code INTEGER,
  stderr TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transcode_profiles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  description TEXT,
  container TEXT NOT NULL,
  video_codec TEXT NOT NULL,
  audio_policy TEXT NOT NULL,
  subtitle_policy TEXT NOT NULL,
  command_template TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transcode_plans (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  profile_id INTEGER REFERENCES transcode_profiles(id),
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS transcode_plan_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  plan_id INTEGER NOT NULL REFERENCES transcode_plans(id) ON DELETE CASCADE,
  file_id INTEGER NOT NULL REFERENCES files(id),
  source_path TEXT NOT NULL,
  target_path TEXT NOT NULL,
  action TEXT NOT NULL,
  reason TEXT,
  command_json TEXT,
  command_display TEXT,
  warnings_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transcode_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  plan_id INTEGER REFERENCES transcode_plans(id),
  name TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  total_items INTEGER NOT NULL DEFAULT 0,
  completed_items INTEGER NOT NULL DEFAULT 0,
  failed_items INTEGER NOT NULL DEFAULT 0,
  canceled_items INTEGER NOT NULL DEFAULT 0,
  current_item_id INTEGER,
  progress_percent REAL NOT NULL DEFAULT 0,
  message TEXT,
  cancel_requested INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_transcode_runs_status ON transcode_runs(status);

CREATE TABLE IF NOT EXISTS transcode_run_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL REFERENCES transcode_runs(id) ON DELETE CASCADE,
  plan_item_id INTEGER REFERENCES transcode_plan_items(id),
  file_id INTEGER REFERENCES files(id),
  status TEXT NOT NULL,
  source_path TEXT NOT NULL,
  target_path TEXT NOT NULL,
  command_json TEXT NOT NULL,
  command_display TEXT NOT NULL,
  log_path TEXT,
  progress_percent REAL NOT NULL DEFAULT 0,
  duration_seconds REAL,
  time_seconds REAL,
  speed TEXT,
  exit_code INTEGER,
  verification_status TEXT,
  verification_message TEXT,
  warnings_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_transcode_run_items_run_id ON transcode_run_items(run_id);
CREATE INDEX IF NOT EXISTS idx_transcode_run_items_status ON transcode_run_items(status);

CREATE TABLE IF NOT EXISTS app_settings (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plex_sync_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  total_items INTEGER NOT NULL DEFAULT 0,
  processed_items INTEGER NOT NULL DEFAULT 0,
  matched_files INTEGER NOT NULL DEFAULT 0,
  unmatched_files INTEGER NOT NULL DEFAULT 0,
  unmatched_parts INTEGER NOT NULL DEFAULT 0,
  message TEXT,
  error_message TEXT,
  cancel_requested INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_plex_sync_jobs_status ON plex_sync_jobs(status);

CREATE TABLE IF NOT EXISTS plex_libraries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  section_key TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  type TEXT,
  agent TEXT,
  scanner TEXT,
  language TEXT,
  uuid TEXT,
  updated_at TEXT NOT NULL,
  raw_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plex_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  rating_key TEXT NOT NULL UNIQUE,
  guid TEXT,
  library_section_key TEXT,
  library_section_title TEXT,
  library_section_type TEXT,
  type TEXT,
  title TEXT,
  sort_title TEXT,
  year INTEGER,
  show_title TEXT,
  season_number INTEGER,
  episode_number INTEGER,
  summary TEXT,
  content_rating TEXT,
  audience_rating REAL,
  user_rating REAL,
  originally_available_at TEXT,
  added_at TEXT,
  updated_at TEXT,
  last_viewed_at TEXT,
  view_count INTEGER,
  thumb TEXT,
  art TEXT,
  collections_json TEXT NOT NULL DEFAULT '[]',
  genres_json TEXT NOT NULL DEFAULT '[]',
  labels_json TEXT NOT NULL DEFAULT '[]',
  raw_json TEXT NOT NULL,
  last_synced_at TEXT NOT NULL,
  is_stale INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_plex_items_library ON plex_items(library_section_key);
CREATE INDEX IF NOT EXISTS idx_plex_items_type ON plex_items(type);
CREATE INDEX IF NOT EXISTS idx_plex_items_title ON plex_items(title);
CREATE INDEX IF NOT EXISTS idx_plex_items_show ON plex_items(show_title);
CREATE INDEX IF NOT EXISTS idx_plex_items_year ON plex_items(year);

CREATE TABLE IF NOT EXISTS plex_media_parts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  plex_item_id INTEGER NOT NULL REFERENCES plex_items(id) ON DELETE CASCADE,
  part_id TEXT,
  file_path TEXT NOT NULL,
  normalized_path TEXT NOT NULL,
  size_bytes INTEGER,
  duration_ms INTEGER,
  container TEXT,
  last_synced_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_plex_media_parts_item ON plex_media_parts(plex_item_id);
CREATE INDEX IF NOT EXISTS idx_plex_media_parts_file ON plex_media_parts(file_path);
CREATE INDEX IF NOT EXISTS idx_plex_media_parts_normalized ON plex_media_parts(normalized_path);

CREATE TABLE IF NOT EXISTS plex_file_matches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  plex_item_id INTEGER REFERENCES plex_items(id) ON DELETE SET NULL,
  plex_media_part_id INTEGER REFERENCES plex_media_parts(id) ON DELETE SET NULL,
  match_status TEXT NOT NULL,
  match_method TEXT NOT NULL,
  path_match_detail TEXT,
  matched_at TEXT NOT NULL,
  UNIQUE(file_id)
);

CREATE INDEX IF NOT EXISTS idx_plex_file_matches_file ON plex_file_matches(file_id);
CREATE INDEX IF NOT EXISTS idx_plex_file_matches_item ON plex_file_matches(plex_item_id);
CREATE INDEX IF NOT EXISTS idx_plex_file_matches_status ON plex_file_matches(match_status);
"""

MIGRATIONS: list[tuple[str, str]] = [
    ("0001_initial_schema", SCHEMA),
    (
        "0002_archive_transcode_plans",
        """
        ALTER TABLE transcode_plans ADD COLUMN archived_at TEXT;
        CREATE INDEX IF NOT EXISTS idx_transcode_plans_archived ON transcode_plans(archived_at);
        """,
    ),
    (
        "0003_publish_transcode_items",
        """
        ALTER TABLE transcode_run_items ADD COLUMN published_at TEXT;
        ALTER TABLE transcode_run_items ADD COLUMN publish_status TEXT;
        ALTER TABLE transcode_run_items ADD COLUMN publish_message TEXT;
        ALTER TABLE transcode_run_items ADD COLUMN published_backup_path TEXT;
        """,
    ),
]


def default_root_payload() -> tuple[str, str]:
    return dumps(DEFAULT_EXTENSIONS), dumps(DEFAULT_EXCLUDES)


def ensure_path_parent(path: str | Path) -> None:
    Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
