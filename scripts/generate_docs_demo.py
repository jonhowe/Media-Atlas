#!/usr/bin/env python3
"""Generate a deterministic, synthetic Media Atlas dataset for documentation screenshots."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
DEFAULT_VERSION = "v1.1.0-demo"
BASE_TIME = "2026-07-01T14:00:00+00:00"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a new synthetic Media Atlas data directory for documentation screenshots."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="A new or empty directory. Existing content is never overwritten.",
    )
    parser.add_argument(
        "--version",
        default=DEFAULT_VERSION,
        help=f"Version metadata shown in the demo UI (default: {DEFAULT_VERSION}).",
    )
    return parser.parse_args()


def prepare_output(output_dir: Path) -> Path:
    output_dir = output_dir.expanduser().resolve()
    if output_dir.exists():
        if not output_dir.is_dir():
            raise ValueError(f"Output path is not a directory: {output_dir}")
        if any(output_dir.iterdir()):
            raise ValueError(f"Output directory must be empty: {output_dir}")
    else:
        output_dir.mkdir(parents=True)
    return output_dir


def configure_environment(output_dir: Path, version: str) -> None:
    os.environ.update(
        {
            "MEDIA_ATLAS_HOST": "127.0.0.1",
            "MEDIA_ATLAS_PORT": "8123",
            "MEDIA_ATLAS_DATA_DIR": str(output_dir / "data"),
            "MEDIA_ATLAS_REPORTS_DIR": str(output_dir / "reports"),
            "MEDIA_ATLAS_LOGS_DIR": str(output_dir / "logs"),
            "MEDIA_ATLAS_TRANSCODE_STAGING_DIR": str(output_dir / "transcode-staging"),
            "MEDIA_ATLAS_TRANSCODE_BACKUP_DIR": str(output_dir / "transcode-backups"),
            "MEDIA_ATLAS_ALLOWED_BROWSE_ROOTS": "/demo/media",
            "MEDIA_ATLAS_AUTH_MODE": "disabled",
            "MEDIA_ATLAS_VERSION": version,
            "MEDIA_ATLAS_GIT_SHA": "docs-demo-0123456789abcdef",
            "MEDIA_ATLAS_BUILD_DATE": "2026-07-01T12:00:00Z",
            "MEDIA_ATLAS_IMAGE_TAG": version,
            "MEDIA_ATLAS_READINESS_MIN_FREE_BYTES": "0",
        }
    )


def generate_demo(output_dir: Path, version: str) -> dict[str, Any]:
    output_dir = prepare_output(output_dir)
    configure_environment(output_dir, version)
    sys.path.insert(0, str(BACKEND_ROOT))

    from app import db
    from app.config import CONFIG, load_config
    from app.logging_config import application_log_path

    fresh_config = load_config()
    for field_name in fresh_config.__dataclass_fields__:
        object.__setattr__(CONFIG, field_name, getattr(fresh_config, field_name))
    db.init_db()

    include_extensions, exclude_patterns = db.default_root_payload()
    with db.connect() as connection:
        root_id = connection.execute(
            """
            INSERT INTO media_roots (
                name, path, enabled, include_extensions_json, exclude_patterns_json,
                created_at, updated_at, last_scanned_at
            ) VALUES (?, ?, 1, ?, ?, ?, ?, ?)
            """,
            (
                "Demo Library",
                "/demo/media",
                include_extensions,
                exclude_patterns,
                "2026-06-01T12:00:00+00:00",
                BASE_TIME,
                BASE_TIME,
            ),
        ).lastrowid

        files = _seed_files(connection, db, int(root_id))
        _seed_plex(connection, db, files)
        _seed_scans(connection)
        plans = _seed_transcodes(connection, db, files, output_dir)
        retention = _seed_retention(connection, db, files, plans)

    _write_logs(output_dir, application_log_path(CONFIG.logs_dir))
    return {
        "output_dir": str(output_dir),
        "database": str(CONFIG.database_path),
        "version": version,
        "files": len(files),
        "plans": plans["count"],
        "retention_candidates": retention["candidate_count"],
    }


def _seed_files(connection: Any, db: Any, root_id: int) -> dict[str, int]:
    specs = [
        {
            "key": "aurora",
            "title": "Aurora Station",
            "year": 2024,
            "filename": "Aurora Station (2024) Bluray-1080p.mkv",
            "size": 18_500_000_000,
            "duration": 7_380.0,
            "container": "mkv",
            "video": "h264",
            "audio": "dts",
            "resolution": "1080p",
            "width": 1920,
            "height": 1080,
            "bitrate": 20_054_000,
            "category": "Easy Win",
            "summary": "High-bitrate H.264 is a strong HEVC conversion candidate.",
            "reasons": ["H.264 source", "High size per hour"],
        },
        {
            "key": "copper",
            "title": "Copper Sky",
            "year": 2022,
            "filename": "Copper Sky (2022) HDTV-1080p.ts",
            "size": 12_800_000_000,
            "duration": 6_900.0,
            "container": "mpegts",
            "video": "mpeg2video",
            "audio": "ac3",
            "resolution": "1080p",
            "width": 1920,
            "height": 1080,
            "bitrate": 14_840_000,
            "category": "Easy Win",
            "summary": "Legacy MPEG-2 video can save substantial space after conversion.",
            "reasons": ["Legacy MPEG-2 video", "Large source"],
        },
        {
            "key": "northbound",
            "title": "Northbound",
            "year": 2021,
            "filename": "Northbound (2021) WEBDL-1080p.mkv",
            "size": 9_600_000_000,
            "duration": 6_420.0,
            "container": "mkv",
            "video": "h264",
            "audio": "eac3",
            "resolution": "1080p",
            "width": 1920,
            "height": 1080,
            "bitrate": 11_960_000,
            "category": "Easy Win",
            "summary": "High-bitrate H.264 is a strong HEVC conversion candidate.",
            "reasons": ["H.264 source", "High bitrate"],
        },
        {
            "key": "paper_moons",
            "title": "Paper Moons",
            "year": 2019,
            "filename": "Paper Moons (2019) WEBRip-1080p.mp4",
            "size": 7_400_000_000,
            "duration": 6_060.0,
            "container": "mp4",
            "video": "hevc",
            "audio": "aac",
            "resolution": "1080p",
            "width": 1920,
            "height": 1080,
            "bitrate": 9_760_000,
            "category": "Remux Only",
            "summary": "Modern codecs only need a safer MKV container.",
            "reasons": ["HEVC already present", "Container normalization"],
        },
        {
            "key": "quiet_circuit",
            "title": "Quiet Circuit",
            "year": 2020,
            "filename": "Quiet Circuit (2020) WEBRip-720p.avi",
            "size": 4_200_000_000,
            "duration": 5_640.0,
            "container": "avi",
            "video": "h264",
            "audio": "ac3",
            "resolution": "720p",
            "width": 1280,
            "height": 720,
            "bitrate": 5_960_000,
            "category": "Remux Only",
            "summary": "The streams are suitable for a lossless MKV remux.",
            "reasons": ["Legacy container", "Compatible streams"],
        },
        {
            "key": "glass_horizon",
            "title": "Glass Horizon",
            "year": 2025,
            "filename": "Glass Horizon (2025) Bluray-2160p.mkv",
            "size": 24_600_000_000,
            "duration": 7_020.0,
            "container": "mkv",
            "video": "hevc",
            "audio": "truehd",
            "resolution": "4K",
            "width": 3840,
            "height": 2160,
            "bitrate": 28_034_000,
            "category": "Review",
            "summary": "Complex HDR media should be reviewed before conversion.",
            "reasons": ["HDR video", "Lossless multichannel audio"],
            "warnings": ["Preserve HDR metadata and TrueHD audio."],
            "is_hdr": 1,
            "hdr_format": "HDR10",
        },
        {
            "key": "signal_harbor",
            "title": "Signal Harbor",
            "year": 2023,
            "filename": "Signal Harbor (2023) Bluray-1080p.mkv",
            "size": 15_100_000_000,
            "duration": 6_780.0,
            "container": "mkv",
            "video": "h264",
            "audio": "dts",
            "resolution": "1080p",
            "width": 1920,
            "height": 1080,
            "bitrate": 17_820_000,
            "category": "Review",
            "summary": "Image subtitles and multiple audio tracks need review.",
            "reasons": ["Multiple audio tracks", "Image subtitles"],
            "warnings": ["Check PGS subtitle compatibility."],
            "image_subtitles": 1,
        },
        {
            "key": "harbor_lights",
            "title": "The Long Signal",
            "show_title": "Harbor Lights",
            "year": 2024,
            "filename": "Harbor Lights - S01E03 - The Long Signal.mkv",
            "directory": "/demo/media/Series/Harbor Lights/Season 01",
            "size": 3_800_000_000,
            "duration": 3_120.0,
            "container": "mkv",
            "video": "hevc",
            "audio": "eac3",
            "resolution": "4K",
            "width": 3840,
            "height": 2160,
            "bitrate": 9_740_000,
            "category": "Review",
            "summary": "Complex episode media should be reviewed before conversion.",
            "reasons": ["4K episode", "Multiple subtitle tracks"],
        },
        {
            "key": "summer_archive",
            "title": "Summer Archive",
            "year": 2020,
            "filename": "Summer Archive (2020) WEBDL-1080p.mkv",
            "size": 5_900_000_000,
            "duration": 6_240.0,
            "container": "mkv",
            "video": "hevc",
            "audio": "eac3",
            "resolution": "1080p",
            "width": 1920,
            "height": 1080,
            "bitrate": 7_560_000,
            "category": "Already Modern",
            "summary": "HEVC with a reasonable bitrate is already efficient.",
            "reasons": ["Modern video codec", "Reasonable bitrate"],
        },
        {
            "key": "ember_trail",
            "title": "Ember Trail",
            "year": 2021,
            "filename": "Ember Trail (2021) WEBDL-1080p.mkv",
            "size": 5_100_000_000,
            "duration": 6_000.0,
            "container": "mkv",
            "video": "av1",
            "audio": "opus",
            "resolution": "1080p",
            "width": 1920,
            "height": 1080,
            "bitrate": 6_800_000,
            "category": "Already Modern",
            "summary": "AV1 and Opus are already efficient.",
            "reasons": ["Modern video and audio codecs"],
        },
        {
            "key": "old_map",
            "title": "The Old Map",
            "year": 2018,
            "filename": "The Old Map (2018) WEBDL-1080p.mkv",
            "size": 6_300_000_000,
            "duration": 5_940.0,
            "container": "mkv",
            "video": "h264",
            "audio": "aac",
            "resolution": "1080p",
            "width": 1920,
            "height": 1080,
            "bitrate": 8_480_000,
            "category": "Missing",
            "summary": "The source file was not found during the latest scan.",
            "reasons": ["Missing from disk"],
            "missing": 1,
        },
        {
            "key": "broken_compass",
            "title": "Broken Compass",
            "year": 2017,
            "filename": "Broken Compass (2017) Archive.mkv",
            "size": 2_700_000_000,
            "duration": None,
            "container": "mkv",
            "video": None,
            "audio": None,
            "resolution": None,
            "width": None,
            "height": None,
            "bitrate": None,
            "category": "Error",
            "summary": "ffprobe could not read this file.",
            "reasons": ["Probe failed"],
            "probe_error": "Invalid data found when processing input.",
        },
    ]

    ids: dict[str, int] = {}
    for index, spec in enumerate(specs, start=1):
        directory = spec.get("directory") or f"/demo/media/Movies/{spec['title']} ({spec['year']})"
        path = f"{directory}/{spec['filename']}"
        duration = spec.get("duration")
        bitrate = spec.get("bitrate")
        cursor = connection.execute(
            """
            INSERT INTO files (
                root_id, path, directory, filename, extension, size_bytes,
                modified_time_ns, first_seen_at, last_seen_at, last_scanned_at,
                is_missing, missing_since, format_name, format_long_name, container,
                duration_seconds, overall_bitrate, primary_video_codec, primary_video_codec_long,
                width, height, resolution_bucket, frame_rate, video_bitrate, pixel_format,
                bit_depth, color_space, color_transfer, color_primaries, hdr_format, is_hdr,
                primary_audio_codec, primary_audio_codec_long, primary_audio_channels,
                primary_audio_channel_layout, primary_audio_language, audio_stream_count,
                subtitle_stream_count, video_stream_count, subtitle_codecs, subtitle_languages,
                has_forced_subtitles, has_image_subtitles, audio_summary, size_per_hour_gb,
                bitrate_mbps, recommendation_category, recommendation_summary,
                recommendation_reasons_json, recommendation_warnings_json, raw_probe_json,
                probe_error, probe_exit_code, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?
            )
            """,
            (
                root_id,
                path,
                directory,
                spec["filename"],
                Path(spec["filename"]).suffix.lower(),
                spec["size"],
                1_720_000_000_000_000_000 + index,
                "2026-05-01T10:00:00+00:00",
                BASE_TIME,
                BASE_TIME,
                spec.get("missing", 0),
                "2026-06-28T10:00:00+00:00" if spec.get("missing") else None,
                spec["container"],
                spec["container"],
                spec["container"],
                duration,
                bitrate,
                spec.get("video"),
                spec.get("video"),
                spec.get("width"),
                spec.get("height"),
                spec.get("resolution"),
                23.976 if duration else None,
                int(bitrate * 0.9) if bitrate else None,
                "yuv420p10le" if spec.get("is_hdr") else "yuv420p",
                10 if spec.get("is_hdr") else 8,
                "bt2020nc" if spec.get("is_hdr") else "bt709",
                "smpte2084" if spec.get("is_hdr") else "bt709",
                "bt2020" if spec.get("is_hdr") else "bt709",
                spec.get("hdr_format"),
                spec.get("is_hdr", 0),
                spec.get("audio"),
                spec.get("audio"),
                8 if spec.get("audio") in {"dts", "truehd"} else 6,
                "7.1" if spec.get("audio") in {"dts", "truehd"} else "5.1",
                "eng",
                2 if spec["key"] in {"signal_harbor", "glass_horizon"} else 1,
                2 if spec["key"] in {"signal_harbor", "harbor_lights"} else 1,
                1 if spec.get("video") else 0,
                "subrip,hdmv_pgs_subtitle" if spec.get("image_subtitles") else "subrip",
                "eng,spa",
                0,
                spec.get("image_subtitles", 0),
                f"{spec.get('audio') or 'unknown'} 5.1",
                round(spec["size"] / 1_000_000_000 / (duration / 3600), 2) if duration else None,
                round(bitrate / 1_000_000, 3) if bitrate else None,
                spec["category"],
                spec["summary"],
                db.dumps(spec["reasons"]),
                db.dumps(spec.get("warnings", [])),
                db.dumps({"format": {"filename": path, "format_name": spec["container"]}}),
                spec.get("probe_error"),
                1 if spec.get("probe_error") else None,
                BASE_TIME,
            ),
        )
        file_id = int(cursor.lastrowid)
        ids[spec["key"]] = file_id
        if spec.get("video"):
            connection.execute(
                """
                INSERT INTO streams (
                    file_id, stream_index, stream_type, codec_name, codec_long_name,
                    profile, language, disposition_default, disposition_forced, width, height,
                    frame_rate, bit_rate, bits_per_raw_sample, pixel_format, color_space,
                    color_transfer, color_primaries, duration_seconds, raw_stream_json
                ) VALUES (?, 0, 'video', ?, ?, 'Main', 'und', 1, 0, ?, ?, 23.976, ?, ?, ?, ?, ?, ?, ?, '{}')
                """,
                (
                    file_id,
                    spec["video"],
                    spec["video"],
                    spec["width"],
                    spec["height"],
                    int(spec["bitrate"] * 0.9),
                    10 if spec.get("is_hdr") else 8,
                    "yuv420p10le" if spec.get("is_hdr") else "yuv420p",
                    "bt2020nc" if spec.get("is_hdr") else "bt709",
                    "smpte2084" if spec.get("is_hdr") else "bt709",
                    "bt2020" if spec.get("is_hdr") else "bt709",
                    duration,
                ),
            )
            connection.execute(
                """
                INSERT INTO streams (
                    file_id, stream_index, stream_type, codec_name, codec_long_name,
                    language, disposition_default, disposition_forced, channels, channel_layout,
                    sample_rate, bit_rate, duration_seconds, raw_stream_json
                ) VALUES (?, 1, 'audio', ?, ?, 'eng', 1, 0, 6, '5.1', 48000, 640000, ?, '{}')
                """,
                (file_id, spec["audio"], spec["audio"], duration),
            )
    connection.execute(
        """
        INSERT INTO chapters (file_id, chapter_index, start_seconds, end_seconds, title, raw_chapter_json)
        VALUES (?, 0, 0, 900, 'Opening', '{}'), (?, 1, 900, 1800, 'Crossing', '{}')
        """,
        (ids["aurora"], ids["aurora"]),
    )
    return ids


def _seed_plex(connection: Any, db: Any, files: dict[str, int]) -> None:
    libraries = [
        ("1", "Demo Movies", "movie", "com.plexapp.agents.demo", "Demo Scanner"),
        ("2", "Demo Series", "show", "com.plexapp.agents.demo", "Demo Scanner"),
    ]
    for section_key, title, library_type, agent, scanner in libraries:
        connection.execute(
            """
            INSERT INTO plex_libraries (
                section_key, title, type, agent, scanner, language, uuid, updated_at, raw_json
            ) VALUES (?, ?, ?, ?, ?, 'en-US', ?, ?, '{}')
            """,
            (section_key, title, library_type, agent, scanner, f"demo-library-{section_key}", BASE_TIME),
        )

    plex_specs = [
        ("aurora", "Aurora Station", 2024, "movie", None, None, 1, ["Science Fiction"], ["Demo Favorites"]),
        ("copper", "Copper Sky", 2022, "movie", None, None, 0, ["Adventure"], ["Demo Watchlist"]),
        ("northbound", "Northbound", 2021, "movie", None, None, 1, ["Drama"], []),
        ("paper_moons", "Paper Moons", 2019, "movie", None, None, 0, ["Drama"], []),
        ("glass_horizon", "Glass Horizon", 2025, "movie", None, None, 0, ["Science Fiction"], ["HDR"]),
        ("signal_harbor", "Signal Harbor", 2023, "movie", None, None, 0, ["Mystery"], []),
        ("summer_archive", "Summer Archive", 2020, "movie", None, None, 1, ["Documentary"], []),
        ("harbor_lights", "The Long Signal", 2024, "episode", 1, 3, 0, ["Drama"], []),
    ]
    for index, (key, title, year, media_type, season, episode, view_count, genres, labels) in enumerate(
        plex_specs, start=1
    ):
        file_id = files[key]
        row = connection.execute("SELECT path, size_bytes, duration_seconds FROM files WHERE id = ?", (file_id,)).fetchone()
        section_key = "2" if media_type == "episode" else "1"
        section_title = "Demo Series" if media_type == "episode" else "Demo Movies"
        item_cursor = connection.execute(
            """
            INSERT INTO plex_items (
                rating_key, guid, library_section_key, library_section_title, library_section_type,
                type, title, sort_title, year, show_title, season_number, episode_number, summary,
                content_rating, audience_rating, user_rating, originally_available_at, added_at,
                updated_at, last_viewed_at, view_count, collections_json, genres_json, labels_json,
                raw_json, last_synced_at, is_stale
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PG', 8.1, 8.4, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, 0)
            """,
            (
                f"demo-{index}",
                f"demo://item/{index}",
                section_key,
                section_title,
                "show" if media_type == "episode" else "movie",
                media_type,
                title,
                title,
                year,
                "Harbor Lights" if media_type == "episode" else None,
                season,
                episode,
                f"Synthetic summary for {title}.",
                f"{year}-01-01",
                "2026-05-01T10:00:00+00:00",
                BASE_TIME,
                "2026-06-15T20:00:00+00:00" if view_count else None,
                view_count,
                db.dumps(["Demo Collection"] if index <= 3 else []),
                db.dumps(genres),
                db.dumps(labels),
                BASE_TIME,
            ),
        )
        item_id = int(item_cursor.lastrowid)
        part_cursor = connection.execute(
            """
            INSERT INTO plex_media_parts (
                plex_item_id, part_id, file_path, normalized_path, size_bytes, duration_ms,
                container, last_synced_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'mkv', ?)
            """,
            (
                item_id,
                f"demo-part-{index}",
                row["path"],
                row["path"],
                row["size_bytes"],
                int((row["duration_seconds"] or 0) * 1000),
                BASE_TIME,
            ),
        )
        connection.execute(
            """
            INSERT INTO plex_file_matches (
                file_id, plex_item_id, plex_media_part_id, match_status, match_method,
                path_match_detail, matched_at
            ) VALUES (?, ?, ?, 'matched', 'exact normalized path', 'Synthetic exact match', ?)
            """,
            (file_id, item_id, int(part_cursor.lastrowid), BASE_TIME),
        )

    connection.execute(
        """
        INSERT INTO app_settings (key, value_json, updated_at) VALUES ('plex_settings', ?, ?)
        """,
        (
            db.dumps(
                {
                    "enabled": True,
                    "server_url": "http://plex.demo.invalid",
                    "token": "synthetic-demo-token",
                    "selected_library_keys": ["1", "2"],
                    "timeout_seconds": 10,
                    "path_mappings": [
                        {"plex_path_prefix": "/demo/media", "media_atlas_path_prefix": "/demo/media"}
                    ],
                }
            ),
            BASE_TIME,
        ),
    )
    connection.execute(
        """
        INSERT INTO plex_sync_jobs (
            status, created_at, started_at, finished_at, total_items, processed_items,
            matched_files, unmatched_files, unmatched_parts, message
        ) VALUES ('succeeded', ?, ?, ?, 8, 8, 8, 4, 0, 'Synthetic Plex sync complete.')
        """,
        ("2026-07-01T13:54:00+00:00", "2026-07-01T13:54:05+00:00", BASE_TIME),
    )


def _seed_scans(connection: Any) -> None:
    success_id = connection.execute(
        """
        INSERT INTO scan_jobs (
            status, started_at, finished_at, created_at, requested_by, total_files_discovered,
            files_skipped, files_probed, files_failed, message
        ) VALUES ('succeeded', ?, ?, ?, 'docs-demo', 12, 9, 3, 0, 'Scan complete.')
        """,
        ("2026-07-01T13:40:00+00:00", "2026-07-01T13:41:18+00:00", "2026-07-01T13:39:58+00:00"),
    ).lastrowid
    failed_id = connection.execute(
        """
        INSERT INTO scan_jobs (
            status, started_at, finished_at, created_at, requested_by, total_files_discovered,
            files_skipped, files_probed, files_failed, current_path, message
        ) VALUES ('failed', ?, ?, ?, 'docs-demo', 12, 0, 11, 1, ?, 'Scan completed with one unreadable file.')
        """,
        (
            "2026-06-28T10:00:00+00:00",
            "2026-06-28T10:02:05+00:00",
            "2026-06-28T09:59:57+00:00",
            "/demo/media/Movies/Broken Compass (2017)/Broken Compass (2017) Archive.mkv",
        ),
    ).lastrowid
    connection.execute(
        """
        INSERT INTO scan_errors (
            scan_job_id, path, error_type, error_message, ffprobe_exit_code, stderr, created_at
        ) VALUES (?, ?, 'probe_error', 'Invalid data found when processing input.', 1, ?, ?)
        """,
        (
            failed_id,
            "/demo/media/Movies/Broken Compass (2017)/Broken Compass (2017) Archive.mkv",
            "[matroska,webm] EBML header parsing failed\nInvalid data found when processing input",
            "2026-06-28T10:01:54+00:00",
        ),
    )
    assert success_id and failed_id


def _seed_transcodes(
    connection: Any, db: Any, files: dict[str, int], output_dir: Path
) -> dict[str, int]:
    fast_profile = connection.execute(
        "SELECT id FROM transcode_profiles WHERE command_template = 'hevc_archive_fast'"
    ).fetchone()["id"]
    remux_profile = connection.execute(
        "SELECT id FROM transcode_profiles WHERE command_template = 'remux_mkv'"
    ).fetchone()["id"]
    now = BASE_TIME
    completed_plan = connection.execute(
        """
        INSERT INTO transcode_plans (
            name, profile_id, status, created_at, updated_at, notes, archived_at
        ) VALUES ('Demo HEVC conversion', ?, 'completed', ?, ?, 'Synthetic completed plan.', ?)
        """,
        (fast_profile, "2026-06-25T09:00:00+00:00", "2026-06-25T11:30:00+00:00", "2026-06-25T12:00:00+00:00"),
    ).lastrowid
    ready_plan = connection.execute(
        """
        INSERT INTO transcode_plans (
            name, profile_id, status, created_at, updated_at, notes
        ) VALUES ('Container cleanup', ?, 'ready', ?, ?, 'Synthetic ready-to-run remux plan.')
        """,
        (remux_profile, "2026-06-30T16:00:00+00:00", now),
    ).lastrowid

    plan_items: dict[str, int] = {}
    for plan_id, key, action, command in [
        (completed_plan, "aurora", "transcode", r"ffmpeg -i Aurora\ Station.mkv -c:v libx265 Aurora\ Station.hevc.mkv"),
        (completed_plan, "northbound", "transcode", "ffmpeg -i Northbound.mkv -c:v libx265 Northbound.hevc.mkv"),
        (ready_plan, "paper_moons", "remux", r"ffmpeg -i Paper\ Moons.mp4 -c copy Paper\ Moons.mkv"),
        (ready_plan, "quiet_circuit", "remux", r"ffmpeg -i Quiet\ Circuit.avi -c copy Quiet\ Circuit.mkv"),
    ]:
        row = connection.execute("SELECT path, filename FROM files WHERE id = ?", (files[key],)).fetchone()
        target = f"/demo/media/Staging/{Path(row['filename']).stem}.transcoded.mkv"
        cursor = connection.execute(
            """
            INSERT INTO transcode_plan_items (
                plan_id, file_id, source_path, target_path, action, reason,
                command_json, command_display, warnings_json
            ) VALUES (?, ?, ?, ?, ?, 'Synthetic documentation example.', ?, ?, '[]')
            """,
            (plan_id, files[key], row["path"], target, action, db.dumps(command.split()), command),
        )
        plan_items[key] = int(cursor.lastrowid)

    completed_run = connection.execute(
        """
        INSERT INTO transcode_runs (
            plan_id, name, status, created_at, started_at, finished_at, total_items,
            completed_items, failed_items, canceled_items, progress_percent, message, archived_at
        ) VALUES (?, 'Demo HEVC conversion', 'succeeded', ?, ?, ?, 2, 2, 0, 0, 100,
                  'All synthetic items completed and were verified.', ?)
        """,
        (
            completed_plan,
            "2026-06-25T09:05:00+00:00",
            "2026-06-25T09:06:00+00:00",
            "2026-06-25T11:24:00+00:00",
            "2026-06-25T12:00:00+00:00",
        ),
    ).lastrowid
    failed_run = connection.execute(
        """
        INSERT INTO transcode_runs (
            plan_id, name, status, created_at, started_at, finished_at, total_items,
            completed_items, failed_items, canceled_items, progress_percent, message
        ) VALUES (?, 'Container cleanup', 'failed', ?, ?, ?, 2, 1, 1, 0, 50,
                  'One synthetic item needs attention.')
        """,
        (
            ready_plan,
            "2026-06-30T16:05:00+00:00",
            "2026-06-30T16:06:00+00:00",
            "2026-06-30T16:18:00+00:00",
        ),
    ).lastrowid

    log_dir = output_dir / "logs" / "transcodes"
    log_dir.mkdir(parents=True, exist_ok=True)
    run_specs = [
        (completed_run, "aurora", "succeeded", 100, 6_900.0, 2_240.0, "1.18x", 0, True, 18_500_000_000, 7_200_000_000),
        (completed_run, "northbound", "succeeded", 100, 6_000.0, 1_980.0, "1.22x", 0, False, 9_600_000_000, 4_100_000_000),
        (failed_run, "paper_moons", "succeeded", 100, 5_700.0, 190.0, "32.1x", 0, False, 7_400_000_000, 7_350_000_000),
        (failed_run, "quiet_circuit", "failed", 12, 5_640.0, 46.0, "0.00x", 1, False, 4_200_000_000, None),
    ]
    first_item_id = None
    for index, (run_id, key, status, progress, duration, encoded, speed, exit_code, published, source_size, output_size) in enumerate(
        run_specs, start=1
    ):
        file_row = connection.execute("SELECT path, filename FROM files WHERE id = ?", (files[key],)).fetchone()
        target = Path("/demo/media/Staging") / f"{Path(file_row['filename']).stem}.transcoded.mkv"
        log_path = log_dir / f"run-{run_id}-item-{index}.log"
        item_cursor = connection.execute(
            """
            INSERT INTO transcode_run_items (
                run_id, plan_item_id, file_id, status, source_path, target_path,
                command_json, command_display, log_path, progress_percent, duration_seconds,
                time_seconds, speed, exit_code, verification_status, verification_message,
                warnings_json, created_at, started_at, finished_at, published_at,
                publish_status, publish_message, published_backup_path, publish_started_at,
                publish_finished_at, publish_step, publish_progress_percent, publish_bytes_done,
                publish_bytes_total, cleanup_status, cleanup_message, cleanup_started_at,
                cleanup_finished_at, staged_deleted_at, backup_deleted_at, source_size_bytes,
                output_size_bytes, validated_at, validation_message
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                run_id,
                plan_items[key],
                files[key],
                status,
                file_row["path"],
                str(target),
                db.dumps(["ffmpeg", "-i", file_row["path"]]),
                f"ffmpeg -i '{file_row['path']}' '{target}'",
                str(log_path),
                progress,
                duration,
                encoded,
                speed,
                exit_code,
                "verified" if status == "succeeded" else "failed",
                "Duration and streams verified." if status == "succeeded" else "ffmpeg exited before verification.",
                "2026-06-25T09:05:00+00:00",
                "2026-06-25T09:06:00+00:00" if run_id == completed_run else "2026-06-30T16:06:00+00:00",
                "2026-06-25T11:24:00+00:00" if run_id == completed_run else "2026-06-30T16:18:00+00:00",
                "2026-06-25T11:30:00+00:00" if published else None,
                "published" if published else None,
                "Synthetic staged output published safely." if published else None,
                "/demo/media/Backups/Aurora Station.backup.mkv" if published else None,
                "2026-06-25T11:25:00+00:00" if published else None,
                "2026-06-25T11:30:00+00:00" if published else None,
                "completed" if published else None,
                100 if published else 0,
                (source_size + (output_size or 0)) if published else 0,
                (source_size + (output_size or 0)) if published else 0,
                "cleaned" if published else None,
                "Staged output and backup removed after validation." if published else None,
                "2026-06-25T11:45:00+00:00" if published else None,
                "2026-06-25T11:46:00+00:00" if published else None,
                "2026-06-25T11:46:00+00:00" if published else None,
                "2026-06-25T11:46:00+00:00" if published else None,
                source_size,
                output_size,
                "2026-06-25T11:40:00+00:00" if published else None,
                "Synthetic output reviewed and approved." if published else None,
            ),
        )
        if first_item_id is None:
            first_item_id = int(item_cursor.lastrowid)
        log_path.write_text(
            "\n".join(
                [
                    "ffmpeg version docs-demo",
                    f"Input #0: {file_row['path']}",
                    "Stream mapping: video -> HEVC, audio -> copy, subtitles -> copy",
                    "frame=143820 fps=55 q=24.0 size=7031MiB time=01:39:54.00 speed=1.18x",
                    "Media Atlas verification: duration and streams passed.",
                    "" if status == "succeeded" else "Synthetic encoder failure: output device unavailable.",
                ]
            ),
            encoding="utf-8",
        )
    connection.execute("UPDATE transcode_runs SET current_item_id = ? WHERE id = ?", (first_item_id, completed_run))
    return {"count": 2, "completed_plan_id": int(completed_plan), "ready_plan_id": int(ready_plan)}


def _seed_retention(
    connection: Any, db: Any, files: dict[str, int], plans: dict[str, int]
) -> dict[str, int]:
    connection_ids: dict[str, int] = {}
    for service_type, name, url, mappings in [
        ("seerr", "Demo Seerr", "http://seerr.demo.invalid", []),
        (
            "radarr",
            "Demo Radarr",
            "http://radarr.demo.invalid",
            [{"source_path_prefix": "/demo/media/Movies", "media_atlas_path_prefix": "/demo/media/Movies"}],
        ),
        (
            "sonarr",
            "Demo Sonarr",
            "http://sonarr.demo.invalid",
            [{"source_path_prefix": "/demo/media/Series", "media_atlas_path_prefix": "/demo/media/Series"}],
        ),
    ]:
        cursor = connection.execute(
            """
            INSERT INTO retention_connections (
                service_type, name, server_url, api_key, enabled, seerr_service_id,
                path_mappings_json, created_at, updated_at
            ) VALUES (?, ?, ?, 'synthetic-demo-key', 1, ?, ?, ?, ?)
            """,
            (
                service_type,
                name,
                url,
                10 if service_type != "seerr" else None,
                db.dumps(mappings),
                "2026-05-01T12:00:00+00:00",
                BASE_TIME,
            ),
        )
        connection_ids[service_type] = int(cursor.lastrowid)

    connection.execute(
        """
        INSERT INTO app_settings (key, value_json, updated_at)
        VALUES ('media_retention_settings', ?, ?)
        """,
        (
            db.dumps(
                {
                    "minimum_unwatched_days": 90,
                    "schedule_enabled": True,
                    "schedule_time": "03:00",
                    "timeout_seconds": 20,
                }
            ),
            BASE_TIME,
        ),
    )
    job_id = connection.execute(
        """
        INSERT INTO retention_analysis_jobs (
            status, trigger_type, created_at, started_at, finished_at, progress_percent,
            current_stage, message, warnings_json, candidate_count, diagnostic_count,
            total_size_bytes
        ) VALUES ('succeeded_with_warnings', 'scheduled', ?, ?, ?, 100, 'complete',
                  'Retention analysis completed with source warnings.', ?, 2, 1, ?)
        """,
        (
            "2026-07-01T03:00:00+00:00",
            "2026-07-01T03:00:02+00:00",
            "2026-07-01T03:02:10+00:00",
            db.dumps([{"source": "Demo Sonarr", "message": "One synthetic source response required a retry."}]),
            30_500_000_000,
        ),
    ).lastrowid

    candidates = [
        ("aurora", "radarr", 101, "movie", "Aurora Station", 2024, 18_500_000_000, 1, 1, "active", ["Avery"], "No qualifying Plex plays were found after the eligibility date."),
        ("signal_harbor", "radarr", 102, "movie", "Signal Harbor", 2023, 15_100_000_000, 1, 0, "diagnostic", ["Morgan", "Riley"], "One managed file could not be mapped exactly; deletion is disabled."),
        ("harbor_lights", "sonarr", 201, "tv", "Harbor Lights", 2024, 12_000_000_000, 3, 3, "active", ["Jordan"], "No qualifying Plex plays were found for the whole series copy."),
    ]
    candidate_ids: dict[str, int] = {}
    for index, (file_key, service_type, service_item_id, media_type, title, year, size, file_count, matched_count, status, requesters, reason) in enumerate(
        candidates, start=1
    ):
        cursor = connection.execute(
            """
            INSERT INTO retention_candidates (
                analysis_job_id, connection_id, service_item_id, seerr_media_id, media_type,
                title, year, tmdb_id, tvdb_id, is_4k, size_bytes, file_count,
                matched_file_count, requesters_json, requests_json, latest_request_at,
                available_since, eligible_since, reason, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                connection_ids[service_type],
                service_item_id,
                5000 + index,
                media_type,
                title,
                year,
                7000 + index if media_type == "movie" else None,
                8000 + index if media_type == "tv" else None,
                1 if title in {"Aurora Station", "Harbor Lights"} else 0,
                size,
                file_count,
                matched_count,
                db.dumps(requesters),
                db.dumps([{"requested_at": "2025-10-01T12:00:00+00:00", "requested_by": requesters[0]}]),
                "2025-10-01T12:00:00+00:00",
                "2025-10-05T12:00:00+00:00",
                "2026-01-05T12:00:00+00:00",
                reason,
                status,
                BASE_TIME,
            ),
        )
        candidate_id = int(cursor.lastrowid)
        candidate_ids[file_key] = candidate_id
        file_row = connection.execute(
            "SELECT path, size_bytes FROM files WHERE id = ?", (files[file_key],)
        ).fetchone()
        connection.execute(
            """
            INSERT INTO retention_candidate_files (
                candidate_id, service_file_id, path, normalized_path, size_bytes,
                date_added, media_atlas_file_id, plex_item_id, plex_rating_key, match_status
            ) VALUES (?, ?, ?, ?, ?, '2025-10-05T12:00:00+00:00', ?,
                      (SELECT plex_item_id FROM plex_file_matches WHERE file_id = ?),
                      (SELECT pi.rating_key FROM plex_items pi JOIN plex_file_matches pfm ON pfm.plex_item_id = pi.id WHERE pfm.file_id = ?), ?)
            """,
            (
                candidate_id,
                9000 + index,
                file_row["path"],
                file_row["path"],
                file_row["size_bytes"],
                files[file_key] if matched_count else None,
                files[file_key],
                files[file_key],
                "matched" if matched_count else "unmatched",
            ),
        )
        for extra_index in range(1, file_count):
            synthetic_path = f"/demo/media/Series/Harbor Lights/Season 01/Harbor Lights - S01E0{extra_index + 3}.mkv"
            connection.execute(
                """
                INSERT INTO retention_candidate_files (
                    candidate_id, service_file_id, path, normalized_path, size_bytes,
                    date_added, media_atlas_file_id, match_status
                ) VALUES (?, ?, ?, ?, ?, '2025-10-05T12:00:00+00:00', ?, 'matched')
                """,
                (
                    candidate_id,
                    9100 + extra_index,
                    synthetic_path,
                    synthetic_path,
                    4_100_000_000,
                    files["harbor_lights"],
                ),
            )

    connection.execute(
        """
        INSERT INTO retention_actions (
            candidate_id, action_type, status, requested_by, created_at, started_at,
            finished_at, transcode_plan_id, snapshot_json, result_json
        ) VALUES (?, 'transcode_plan', 'succeeded', 'docs-demo', ?, ?, ?, ?, ?, ?)
        """,
        (
            candidate_ids["aurora"],
            "2026-06-25T08:55:00+00:00",
            "2026-06-25T08:55:02+00:00",
            "2026-06-25T08:55:05+00:00",
            plans["completed_plan_id"],
            db.dumps({"title": "Aurora Station", "size_bytes": 18_500_000_000}),
            db.dumps({"transcode_plan_id": plans["completed_plan_id"], "file_ids": [files["aurora"]]}),
        ),
    )
    return {"candidate_count": len(candidates), "analysis_job_id": int(job_id)}


def _write_logs(output_dir: Path, application_log: Path) -> None:
    application_log.parent.mkdir(parents=True, exist_ok=True)
    entries = [
        {
            "timestamp": "2026-07-01T13:39:58+00:00",
            "level": "info",
            "logger": "media_atlas.scanner",
            "message": "Synthetic scan queued.",
            "job_id": 1,
        },
        {
            "timestamp": "2026-07-01T13:41:18+00:00",
            "level": "info",
            "logger": "media_atlas.scanner",
            "message": "Synthetic scan completed successfully.",
            "job_id": 1,
        },
        {
            "timestamp": "2026-07-01T13:54:05+00:00",
            "level": "info",
            "logger": "media_atlas.plex",
            "message": "Synthetic Plex sync started.",
            "job_id": 1,
        },
        {
            "timestamp": "2026-07-01T14:00:00+00:00",
            "level": "info",
            "logger": "media_atlas.plex",
            "message": "Synthetic Plex sync completed: 8 files matched.",
            "job_id": 1,
        },
        {
            "timestamp": "2026-07-01T14:02:10+00:00",
            "level": "warning",
            "logger": "media_atlas.retention",
            "message": "Synthetic source response succeeded after one retry.",
            "job_id": 1,
        },
        {
            "timestamp": "2026-07-01T14:03:00+00:00",
            "level": "info",
            "logger": "media_atlas.startup",
            "message": "Media Atlas documentation demo ready.",
        },
    ]
    application_log.write_text(
        "".join(f"{json.dumps(entry, separators=(',', ':'))}\n" for entry in entries),
        encoding="utf-8",
    )
    (output_dir / "reports").mkdir(parents=True, exist_ok=True)


def main() -> int:
    args = parse_args()
    try:
        summary = generate_demo(args.output_dir, args.version)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
