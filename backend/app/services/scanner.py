from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from .. import db
from ..config import CONFIG
from .ffprobe import ProbeError, probe_file
from .file_discovery import discover_media_files
from .metadata import normalize_probe
from .recommendations import recommend


class ScanManager:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def recover_startup_jobs(self) -> None:
        db.execute(
            """
            UPDATE scan_jobs
            SET status = 'interrupted',
                finished_at = COALESCE(finished_at, ?),
                current_path = NULL,
                message = 'Backend restarted while this scan was active.'
            WHERE status = 'running'
            """,
            (db.utc_now(),),
        )
        queued = db.query_one("SELECT id FROM scan_jobs WHERE status = 'queued' ORDER BY id LIMIT 1")
        if queued:
            async with self._lock:
                if self._task is None or self._task.done():
                    self._task = asyncio.create_task(self._run_scan(queued["id"]))

    async def start_scan(self) -> dict[str, Any]:
        async with self._lock:
            running = db.query_one("SELECT * FROM scan_jobs WHERE status IN ('queued','running') ORDER BY id LIMIT 1")
            if running:
                return running
            now = db.utc_now()
            job_id = db.execute(
                """
                INSERT INTO scan_jobs (status, created_at, message)
                VALUES ('queued', ?, 'Scan queued.')
                """,
                (now,),
            )
            self._task = asyncio.create_task(self._run_scan(job_id))
            return db.query_one("SELECT * FROM scan_jobs WHERE id = ?", (job_id,)) or {"id": job_id}

    async def retry_scan(self, job_id: int) -> dict[str, Any]:
        scan = db.query_one("SELECT * FROM scan_jobs WHERE id = ?", (job_id,))
        if not scan:
            raise ValueError("Scan not found.")
        if scan["status"] in {"queued", "running"}:
            return scan
        db.execute(
            """
            UPDATE scan_jobs
            SET status = 'queued',
                started_at = NULL,
                finished_at = NULL,
                total_files_discovered = 0,
                files_skipped = 0,
                files_probed = 0,
                files_failed = 0,
                current_path = NULL,
                message = 'Scan retry queued.',
                cancel_requested = 0
            WHERE id = ?
            """,
            (job_id,),
        )
        async with self._lock:
            if self._task is None or self._task.done():
                self._task = asyncio.create_task(self._run_scan(job_id))
        return db.query_one("SELECT * FROM scan_jobs WHERE id = ?", (job_id,)) or {"id": job_id}

    def cancel_scan(self, job_id: int) -> None:
        db.execute(
            "UPDATE scan_jobs SET cancel_requested = 1, message = 'Cancel requested.' WHERE id = ?",
            (job_id,),
        )

    async def _run_scan(self, job_id: int) -> None:
        started_at = db.utc_now()
        db.execute(
            """
            UPDATE scan_jobs
            SET status = 'running', started_at = ?, message = 'Discovering media files.'
            WHERE id = ?
            """,
            (started_at, job_id),
        )
        roots = db.query_all("SELECT * FROM media_roots WHERE enabled = 1 ORDER BY name")
        try:
            candidates, canceled = await asyncio.to_thread(self._discover_candidates, job_id, roots)
            if canceled:
                return
            semaphore = asyncio.Semaphore(CONFIG.scanner.concurrency)
            await asyncio.gather(
                *(self._probe_candidate(job_id, root, path, stat_result, semaphore) for root, path, stat_result in candidates)
            )
            status = "canceled" if self._is_cancel_requested(job_id) else "succeeded"
            message = "Scan canceled." if status == "canceled" else "Scan complete."
            self._finish(job_id, status, message)
        except Exception as exc:
            self._finish(job_id, "failed", f"Scan failed: {exc}")

    def _discover_candidates(
        self,
        job_id: int,
        roots: list[dict[str, Any]],
    ) -> tuple[list[tuple[dict[str, Any], Path, os.stat_result]], bool]:
        candidates: list[tuple[dict[str, Any], Path, os.stat_result]] = []
        for root in roots:
            if self._is_cancel_requested(job_id):
                self._finish(job_id, "canceled", "Scan canceled.")
                return candidates, True
            root_path = Path(root["path"]).expanduser()
            if not root_path.exists() or not root_path.is_dir():
                self._record_error(
                    job_id,
                    str(root_path),
                    "Root path unavailable",
                    "Root was unavailable; existing files were not marked missing.",
                )
                continue

            seen: set[str] = set()
            paths = list(discover_media_files(root))
            db.execute(
                """
                UPDATE scan_jobs
                SET total_files_discovered = total_files_discovered + ?,
                    message = ?
                WHERE id = ?
                """,
                (len(paths), f"Discovered {len(paths)} files under {root['name']}.", job_id),
            )
            for path in paths:
                if self._is_cancel_requested(job_id):
                    self._finish(job_id, "canceled", "Scan canceled.")
                    return candidates, True
                resolved = str(path.resolve())
                seen.add(resolved)
                try:
                    stat_result = path.stat()
                except OSError as exc:
                    self._record_error(job_id, resolved, "File not readable", str(exc))
                    self._increment(job_id, "files_failed")
                    continue
                existing = db.query_one("SELECT * FROM files WHERE path = ?", (resolved,))
                if (
                    existing
                    and existing["size_bytes"] == stat_result.st_size
                    and existing["modified_time_ns"] == stat_result.st_mtime_ns
                    and not existing.get("probe_error")
                ):
                    now = db.utc_now()
                    db.execute(
                        """
                        UPDATE files
                        SET last_seen_at = ?, is_missing = 0, missing_since = NULL, updated_at = ?
                        WHERE id = ?
                        """,
                        (now, now, existing["id"]),
                    )
                    self._increment(job_id, "files_skipped")
                else:
                    candidates.append((root, path, stat_result))

            if CONFIG.scanner.mark_missing_files:
                self._mark_missing(root["id"], seen)
            db.execute(
                "UPDATE media_roots SET last_scanned_at = ?, updated_at = ? WHERE id = ?",
                (db.utc_now(), db.utc_now(), root["id"]),
            )
        return candidates, False

    async def _probe_candidate(
        self,
        job_id: int,
        root: dict[str, Any],
        path: Path,
        stat_result: os.stat_result,
        semaphore: asyncio.Semaphore,
    ) -> None:
        async with semaphore:
            if self._is_cancel_requested(job_id):
                return
            resolved = str(path.resolve())
            db.execute(
                "UPDATE scan_jobs SET current_path = ?, message = 'Probing media.' WHERE id = ?",
                (resolved, job_id),
            )
            try:
                raw = await probe_file(path)
                await asyncio.to_thread(self._save_probe_success, job_id, root, path, stat_result, raw)
            except ProbeError as exc:
                await asyncio.to_thread(self._save_probe_error, job_id, root, path, stat_result, exc)
                await asyncio.to_thread(self._increment, job_id, "files_failed")
            except Exception as exc:
                await asyncio.to_thread(self._record_error, job_id, resolved, "Scan processing error", str(exc))
                await asyncio.to_thread(self._increment, job_id, "files_failed")

    def _save_probe_success(
        self,
        job_id: int,
        root: dict[str, Any],
        path: Path,
        stat_result: os.stat_result,
        raw: dict[str, Any],
    ) -> None:
        normalized = normalize_probe(path.resolve(), stat_result, raw)
        normalized["root_id"] = root["id"]
        recommendation = recommend(normalized)
        file_id = self._upsert_file(normalized, recommendation)
        self._replace_streams(file_id, normalized["streams"], normalized["chapters"])
        self._increment(job_id, "files_probed")

    def _upsert_file(self, item: dict[str, Any], recommendation: dict[str, Any]) -> int:
        now = db.utc_now()
        existing = db.query_one("SELECT id, first_seen_at FROM files WHERE path = ?", (item["path"],))
        first_seen_at = existing["first_seen_at"] if existing else now
        columns = [
            "root_id",
            "path",
            "directory",
            "filename",
            "extension",
            "size_bytes",
            "modified_time_ns",
            "created_time_ns",
            "first_seen_at",
            "last_seen_at",
            "last_scanned_at",
            "is_missing",
            "missing_since",
            "format_name",
            "format_long_name",
            "container",
            "duration_seconds",
            "overall_bitrate",
            "primary_video_codec",
            "primary_video_codec_long",
            "primary_video_profile",
            "width",
            "height",
            "resolution_bucket",
            "frame_rate",
            "video_bitrate",
            "pixel_format",
            "bit_depth",
            "color_space",
            "color_transfer",
            "color_primaries",
            "hdr_format",
            "is_hdr",
            "is_interlaced",
            "primary_audio_codec",
            "primary_audio_codec_long",
            "primary_audio_channels",
            "primary_audio_channel_layout",
            "primary_audio_language",
            "audio_stream_count",
            "subtitle_stream_count",
            "video_stream_count",
            "subtitle_codecs",
            "subtitle_languages",
            "has_forced_subtitles",
            "has_image_subtitles",
            "audio_summary",
            "size_per_hour_gb",
            "bitrate_mbps",
            "recommendation_category",
            "recommendation_summary",
            "recommendation_reasons_json",
            "recommendation_warnings_json",
            "raw_probe_json",
            "probe_error",
            "probe_exit_code",
            "updated_at",
        ]
        values = {
            **item,
            "first_seen_at": first_seen_at,
            "last_seen_at": now,
            "last_scanned_at": now,
            "is_missing": 0,
            "missing_since": None,
            "recommendation_category": recommendation["category"],
            "recommendation_summary": recommendation["summary"],
            "recommendation_reasons_json": db.dumps(recommendation["reasons"]),
            "recommendation_warnings_json": db.dumps(recommendation["warnings"]),
            "probe_error": None,
            "probe_exit_code": None,
            "updated_at": now,
        }
        if existing:
            assignments = ", ".join(f"{column} = ?" for column in columns if column != "path")
            params = tuple(values[column] for column in columns if column != "path") + (item["path"],)
            db.execute(f"UPDATE files SET {assignments} WHERE path = ?", params)
            return int(existing["id"])

        placeholders = ", ".join("?" for _ in columns)
        file_id = db.execute(
            f"INSERT INTO files ({', '.join(columns)}) VALUES ({placeholders})",
            tuple(values[column] for column in columns),
        )
        return file_id

    def _replace_streams(self, file_id: int, streams: list[dict[str, Any]], chapters: list[dict[str, Any]]) -> None:
        stream_columns = [
            "file_id",
            "stream_index",
            "stream_type",
            "codec_name",
            "codec_long_name",
            "profile",
            "language",
            "title",
            "disposition_default",
            "disposition_forced",
            "width",
            "height",
            "frame_rate",
            "channels",
            "channel_layout",
            "sample_rate",
            "bit_rate",
            "bits_per_raw_sample",
            "pixel_format",
            "color_space",
            "color_transfer",
            "color_primaries",
            "duration_seconds",
            "raw_stream_json",
        ]
        chapter_columns = ["file_id", "chapter_index", "start_seconds", "end_seconds", "title", "raw_chapter_json"]
        with db.connect() as connection:
            connection.execute("DELETE FROM streams WHERE file_id = ?", (file_id,))
            connection.execute("DELETE FROM chapters WHERE file_id = ?", (file_id,))
            if streams:
                connection.executemany(
                    f"INSERT INTO streams ({', '.join(stream_columns)}) VALUES ({', '.join('?' for _ in stream_columns)})",
                    [tuple({**stream, "file_id": file_id}[column] for column in stream_columns) for stream in streams],
                )
            if chapters:
                connection.executemany(
                    f"INSERT INTO chapters ({', '.join(chapter_columns)}) VALUES ({', '.join('?' for _ in chapter_columns)})",
                    [tuple({**chapter, "file_id": file_id}[column] for column in chapter_columns) for chapter in chapters],
                )

    def _save_probe_error(
        self,
        job_id: int,
        root: dict[str, Any],
        path: Path,
        stat_result: os.stat_result,
        exc: ProbeError,
    ) -> None:
        resolved = str(path.resolve())
        now = db.utc_now()
        existing = db.query_one("SELECT id, first_seen_at FROM files WHERE path = ?", (resolved,))
        recommendation = {
            "category": "Error",
            "summary": "The file could not be probed.",
            "reasons": [exc.stderr],
            "warnings": [],
        }
        columns = [
            "root_id",
            "path",
            "directory",
            "filename",
            "extension",
            "size_bytes",
            "modified_time_ns",
            "created_time_ns",
            "first_seen_at",
            "last_seen_at",
            "last_scanned_at",
            "is_missing",
            "missing_since",
            "recommendation_category",
            "recommendation_summary",
            "recommendation_reasons_json",
            "recommendation_warnings_json",
            "probe_error",
            "probe_exit_code",
            "updated_at",
        ]
        values = {
            "root_id": root["id"],
            "path": resolved,
            "directory": str(path.parent),
            "filename": path.name,
            "extension": path.suffix.lower(),
            "size_bytes": stat_result.st_size,
            "modified_time_ns": stat_result.st_mtime_ns,
            "created_time_ns": getattr(stat_result, "st_birthtime", None)
            and int(stat_result.st_birthtime * 1_000_000_000),
            "first_seen_at": existing["first_seen_at"] if existing else now,
            "last_seen_at": now,
            "last_scanned_at": now,
            "is_missing": 0,
            "missing_since": None,
            "recommendation_category": recommendation["category"],
            "recommendation_summary": recommendation["summary"],
            "recommendation_reasons_json": db.dumps(recommendation["reasons"]),
            "recommendation_warnings_json": db.dumps(recommendation["warnings"]),
            "probe_error": exc.stderr,
            "probe_exit_code": exc.exit_code,
            "updated_at": now,
        }
        if existing:
            assignments = ", ".join(f"{column} = ?" for column in columns if column != "path")
            params = tuple(values[column] for column in columns if column != "path") + (resolved,)
            db.execute(f"UPDATE files SET {assignments} WHERE path = ?", params)
        else:
            db.execute(
                f"INSERT INTO files ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                tuple(values[column] for column in columns),
            )
        self._record_error(job_id, resolved, "ffprobe failed", exc.stderr, exc.exit_code, exc.stderr)

    def _record_error(
        self,
        job_id: int,
        path: str,
        error_type: str,
        error_message: str,
        exit_code: int | None = None,
        stderr: str | None = None,
    ) -> None:
        db.execute(
            """
            INSERT INTO scan_errors (
                scan_job_id, path, error_type, error_message, ffprobe_exit_code, stderr, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, path, error_type, error_message, exit_code, stderr, db.utc_now()),
        )

    def _mark_missing(self, root_id: int, seen: set[str]) -> None:
        existing = db.query_all("SELECT id, path, is_missing FROM files WHERE root_id = ?", (root_id,))
        now = db.utc_now()
        for row in existing:
            if row["path"] not in seen and not row["is_missing"]:
                recommendation = recommend({"is_missing": 1})
                db.execute(
                    """
                    UPDATE files
                    SET is_missing = 1,
                        missing_since = ?,
                        recommendation_category = ?,
                        recommendation_summary = ?,
                        recommendation_reasons_json = ?,
                        recommendation_warnings_json = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        now,
                        recommendation["category"],
                        recommendation["summary"],
                        db.dumps(recommendation["reasons"]),
                        db.dumps(recommendation["warnings"]),
                        now,
                        row["id"],
                    ),
                )

    def _increment(self, job_id: int, column: str) -> None:
        if column not in {"files_skipped", "files_probed", "files_failed"}:
            return
        db.execute(f"UPDATE scan_jobs SET {column} = {column} + 1 WHERE id = ?", (job_id,))

    def _finish(self, job_id: int, status: str, message: str) -> None:
        db.execute(
            """
            UPDATE scan_jobs
            SET status = ?, finished_at = ?, message = ?, current_path = NULL
            WHERE id = ?
            """,
            (status, db.utc_now(), message, job_id),
        )

    def _is_cancel_requested(self, job_id: int) -> bool:
        row = db.query_one("SELECT cancel_requested FROM scan_jobs WHERE id = ?", (job_id,))
        return bool(row and row["cancel_requested"])
