from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from .. import db
from ..config import CONFIG
from .ffprobe import ProbeError, probe_file
from .paths import safe_shell_join
from .recommendations import transcode_warnings


class TranscodeManager:
    def __init__(self) -> None:
        self._worker_task: asyncio.Task | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._running_item_id: int | None = None
        self._lock = asyncio.Lock()

    async def ensure_worker(self) -> None:
        async with self._lock:
            if self._worker_task is None or self._worker_task.done():
                self._worker_task = asyncio.create_task(self._worker_loop())

    async def create_run(self, plan_id: int, name: str | None = None) -> dict[str, Any]:
        plan = db.query_one("SELECT * FROM transcode_plans WHERE id = ?", (plan_id,))
        if not plan:
            raise ValueError("Transcode plan not found.")
        items = db.query_all("SELECT * FROM transcode_plan_items WHERE plan_id = ? ORDER BY id", (plan_id,))
        runnable = [item for item in items if item.get("command_json")]
        if not runnable:
            raise ValueError("Plan has no runnable items.")
        now = db.utc_now()
        run_id = db.execute(
            """
            INSERT INTO transcode_runs (
                plan_id, name, status, created_at, total_items, message
            )
            VALUES (?, ?, 'queued', ?, ?, 'Run queued.')
            """,
            (plan_id, name or f"Run for {plan['name']}", now, len(runnable)),
        )
        rows = []
        for item in runnable:
            rows.append(
                (
                    run_id,
                    item["id"],
                    item["file_id"],
                    "queued",
                    item["source_path"],
                    item["target_path"],
                    item["command_json"],
                    item["command_display"],
                    item["warnings_json"],
                    now,
                )
            )
        db.executemany(
            """
            INSERT INTO transcode_run_items (
                run_id, plan_item_id, file_id, status, source_path, target_path,
                command_json, command_display, warnings_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        await self.ensure_worker()
        return db.query_one("SELECT * FROM transcode_runs WHERE id = ?", (run_id,)) or {"id": run_id}

    def cancel_run(self, run_id: int) -> None:
        db.execute(
            "UPDATE transcode_runs SET cancel_requested = 1, message = 'Cancel requested.' WHERE id = ?",
            (run_id,),
        )
        db.execute(
            "UPDATE transcode_run_items SET status = 'canceled', finished_at = ? WHERE run_id = ? AND status = 'queued'",
            (db.utc_now(), run_id),
        )
        if self._process and self._running_item_id:
            item = db.query_one("SELECT run_id FROM transcode_run_items WHERE id = ?", (self._running_item_id,))
            if item and item["run_id"] == run_id:
                self._process.terminate()

    async def retry_run(self, run_id: int) -> dict[str, Any]:
        now = db.utc_now()
        db.execute(
            """
            UPDATE transcode_run_items
            SET status = 'queued',
                progress_percent = 0,
                time_seconds = NULL,
                speed = NULL,
                exit_code = NULL,
                verification_status = NULL,
                verification_message = NULL,
                started_at = NULL,
                finished_at = NULL
            WHERE run_id = ? AND status IN ('failed', 'interrupted', 'verification_failed', 'canceled')
            """,
            (run_id,),
        )
        db.execute(
            """
            UPDATE transcode_runs
            SET status = 'queued',
                finished_at = NULL,
                cancel_requested = 0,
                message = 'Retry queued.',
                started_at = COALESCE(started_at, ?)
            WHERE id = ?
            """,
            (now, run_id),
        )
        await self.ensure_worker()
        return db.query_one("SELECT * FROM transcode_runs WHERE id = ?", (run_id,)) or {"id": run_id}

    async def _worker_loop(self) -> None:
        while True:
            item = db.query_one(
                """
                SELECT tri.*, tr.cancel_requested
                FROM transcode_run_items tri
                JOIN transcode_runs tr ON tr.id = tri.run_id
                WHERE tri.status = 'queued' AND tr.cancel_requested = 0
                ORDER BY tri.id
                LIMIT 1
                """
            )
            if not item:
                return
            await self._run_item(item)

    async def _run_item(self, item: dict[str, Any]) -> None:
        run_id = item["run_id"]
        now = db.utc_now()
        db.execute(
            """
            UPDATE transcode_runs
            SET status = 'running',
                started_at = COALESCE(started_at, ?),
                current_item_id = ?,
                message = ?
            WHERE id = ?
            """,
            (now, item["id"], f"Running {Path(item['source_path']).name}", run_id),
        )
        log_path = CONFIG.logs_dir / "transcodes" / f"run-{run_id}" / f"item-{item['id']}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        db.execute(
            """
            UPDATE transcode_run_items
            SET status = 'running', started_at = ?, log_path = ?
            WHERE id = ?
            """,
            (now, str(log_path), item["id"]),
        )
        self._running_item_id = item["id"]
        command = json.loads(item["command_json"])
        duration = item.get("duration_seconds") or self._source_duration(item["file_id"])
        try:
            Path(item["target_path"]).parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as log_file:
                log_file.write(f"[{now}] {safe_shell_join(command)}\n\n")
                exit_code = await self._execute_ffmpeg(item, command, log_file, duration)
            if self._is_run_cancel_requested(run_id):
                self._finish_item(item["id"], "canceled", exit_code, "canceled", "Canceled by user.")
            elif exit_code == 0:
                verification_status, verification_message = await self._verify_output(item, duration)
                status = "succeeded" if verification_status == "verified" else "verification_failed"
                self._finish_item(item["id"], status, exit_code, verification_status, verification_message)
            else:
                self._finish_item(item["id"], "failed", exit_code, "not_run", f"ffmpeg exited with {exit_code}.")
        except Exception as exc:
            self._finish_item(item["id"], "failed", None, "not_run", str(exc))
        finally:
            self._process = None
            self._running_item_id = None
            self._refresh_run_status(run_id)

    async def _execute_ffmpeg(
        self,
        item: dict[str, Any],
        command: list[str],
        log_file: Any,
        duration: float | None,
    ) -> int:
        command = self._with_progress(command)
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._process = proc

        async def read_stdout() -> None:
            assert proc.stdout is not None
            progress: dict[str, str] = {}
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", "replace").strip()
                if "=" not in text:
                    continue
                key, value = text.split("=", 1)
                progress[key] = value
                if key == "progress":
                    self._store_progress(item["id"], progress, duration)
                    progress = {}

        async def read_stderr() -> None:
            assert proc.stderr is not None
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                log_file.write(line.decode("utf-8", "replace"))
                log_file.flush()

        await asyncio.gather(read_stdout(), read_stderr())
        return await proc.wait()

    def _with_progress(self, command: list[str]) -> list[str]:
        if len(command) < 2:
            return command
        return [
            command[0],
            "-hide_banner",
            "-nostdin",
            "-nostats",
            "-progress",
            "pipe:1",
            *command[1:],
        ]

    def _store_progress(self, item_id: int, progress: dict[str, str], duration: float | None) -> None:
        time_seconds = None
        if progress.get("out_time_ms"):
            try:
                time_seconds = int(progress["out_time_ms"]) / 1_000_000
            except ValueError:
                pass
        percent = 0.0
        if duration and time_seconds is not None and duration > 0:
            percent = min(99.0, round((time_seconds / duration) * 100, 2))
        db.execute(
            """
            UPDATE transcode_run_items
            SET progress_percent = ?, time_seconds = ?, speed = ?
            WHERE id = ?
            """,
            (percent, time_seconds, progress.get("speed"), item_id),
        )
        item = db.query_one("SELECT run_id FROM transcode_run_items WHERE id = ?", (item_id,))
        if item:
            self._refresh_run_progress(item["run_id"])

    def _refresh_run_progress(self, run_id: int) -> None:
        rows = db.query_all("SELECT progress_percent FROM transcode_run_items WHERE run_id = ?", (run_id,))
        total = len(rows)
        progress = round(sum(row["progress_percent"] or 0 for row in rows) / total, 2) if total else 0
        db.execute(
            """
            UPDATE transcode_runs
            SET progress_percent = ?
            WHERE id = ?
            """,
            (progress, run_id),
        )

    async def _verify_output(self, item: dict[str, Any], source_duration: float | None) -> tuple[str, str]:
        target = Path(item["target_path"])
        if not target.exists() or target.stat().st_size == 0:
            return "failed", "Output file was not created or is empty."
        try:
            raw = await probe_file(target)
        except ProbeError as exc:
            return "failed", f"Output ffprobe failed: {exc.stderr}"
        target_duration = None
        try:
            target_duration = float((raw.get("format") or {}).get("duration"))
        except (TypeError, ValueError):
            target_duration = None
        if source_duration and target_duration:
            tolerance = max(
                CONFIG.transcoder.duration_tolerance_seconds,
                source_duration * CONFIG.transcoder.duration_tolerance_percent,
            )
            if abs(source_duration - target_duration) > tolerance:
                return (
                    "failed",
                    f"Duration mismatch: source {source_duration:.2f}s, output {target_duration:.2f}s.",
                )
        return "verified", "Output exists, probes successfully, and duration is within tolerance."

    def _finish_item(
        self,
        item_id: int,
        status: str,
        exit_code: int | None,
        verification_status: str,
        verification_message: str,
    ) -> None:
        db.execute(
            """
            UPDATE transcode_run_items
            SET status = ?,
                progress_percent = CASE WHEN ? = 'succeeded' THEN 100 ELSE progress_percent END,
                exit_code = ?,
                verification_status = ?,
                verification_message = ?,
                finished_at = ?
            WHERE id = ?
            """,
            (status, status, exit_code, verification_status, verification_message, db.utc_now(), item_id),
        )

    def _refresh_run_status(self, run_id: int) -> None:
        rows = db.query_all("SELECT status, progress_percent FROM transcode_run_items WHERE run_id = ?", (run_id,))
        total = len(rows)
        completed = sum(1 for row in rows if row["status"] == "succeeded")
        failed = sum(1 for row in rows if row["status"] in {"failed", "verification_failed", "interrupted"})
        canceled = sum(1 for row in rows if row["status"] == "canceled")
        running = any(row["status"] == "running" for row in rows)
        queued = any(row["status"] == "queued" for row in rows)
        if running:
            status = "running"
        elif queued:
            status = "queued"
        elif failed:
            status = "failed"
        elif canceled and completed + canceled == total:
            status = "canceled"
        else:
            status = "succeeded"
        progress = round(sum(row["progress_percent"] or 0 for row in rows) / total, 2) if total else 0
        message = {
            "running": "Transcode run is active.",
            "queued": "Transcode run is queued.",
            "failed": "Transcode run finished with failures.",
            "canceled": "Transcode run was canceled.",
            "succeeded": "Transcode run complete.",
        }[status]
        finished_at = db.utc_now() if status in {"failed", "canceled", "succeeded"} else None
        db.execute(
            """
            UPDATE transcode_runs
            SET status = ?,
                completed_items = ?,
                failed_items = ?,
                canceled_items = ?,
                progress_percent = ?,
                message = ?,
                current_item_id = CASE WHEN ? IN ('running', 'queued') THEN current_item_id ELSE NULL END,
                finished_at = COALESCE(?, finished_at)
            WHERE id = ?
            """,
            (status, completed, failed, canceled, progress, message, status, finished_at, run_id),
        )

    def _source_duration(self, file_id: int | None) -> float | None:
        if not file_id:
            return None
        row = db.query_one("SELECT duration_seconds FROM files WHERE id = ?", (file_id,))
        return row and row["duration_seconds"]

    def _is_run_cancel_requested(self, run_id: int) -> bool:
        row = db.query_one("SELECT cancel_requested FROM transcode_runs WHERE id = ?", (run_id,))
        return bool(row and row["cancel_requested"])


def build_command(profile: dict[str, Any], source_path: str, target_path: str) -> list[str] | None:
    template = profile["command_template"]
    ffmpeg = CONFIG.transcoder.ffmpeg_path
    if template == "manual_review":
        return None
    if template == "remux_mkv":
        return [ffmpeg, "-n", "-i", source_path, "-map", "0", "-c", "copy", target_path]
    if template == "hevc_archive":
        return [
            ffmpeg,
            "-n",
            "-i",
            source_path,
            "-map",
            "0",
            "-c:v",
            "libx265",
            "-crf",
            "20",
            "-preset",
            "medium",
            "-c:a",
            "copy",
            "-c:s",
            "copy",
            target_path,
        ]
    if template == "h264_compat":
        return [
            ffmpeg,
            "-n",
            "-i",
            source_path,
            "-map",
            "0",
            "-c:v",
            "libx264",
            "-crf",
            "20",
            "-preset",
            "slow",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-c:s",
            "mov_text",
            target_path,
        ]
    return None


def plan_target_path(file_row: dict[str, Any], profile: dict[str, Any]) -> str:
    root = db.query_one("SELECT * FROM media_roots WHERE id = ?", (file_row["root_id"],))
    root_name = (root or {}).get("name") or "media"
    source = Path(file_row["path"])
    try:
        rel = source.relative_to(Path((root or {}).get("path", source.parent)))
    except ValueError:
        rel = Path(source.name)
    extension = profile["container"] if profile["container"] != "none" else source.suffix.lstrip(".")
    rel_target = rel.with_suffix(f".{extension}")
    base = CONFIG.transcoder.staging_dir / _safe_segment(root_name) / rel_target
    return _dedupe_path(base)


def _safe_segment(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in value)
    return cleaned.strip("._") or "media"


def _dedupe_path(path: Path) -> str:
    if not path.exists():
        return str(path)
    stem = path.stem
    suffix = path.suffix
    for index in range(1, 10_000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return str(candidate)
    raise ValueError(f"Could not find available target path for {path}")


def create_plan(name: str, profile_id: int, file_ids: list[int], notes: str | None = None) -> dict[str, Any]:
    profile = db.query_one("SELECT * FROM transcode_profiles WHERE id = ?", (profile_id,))
    if not profile:
        raise ValueError("Transcode profile not found.")
    files = []
    for file_id in file_ids:
        row = db.query_one("SELECT * FROM files WHERE id = ?", (file_id,))
        if row:
            files.append(row)
    if not files:
        raise ValueError("No media files found for the selected ids.")

    now = db.utc_now()
    plan_id = db.execute(
        """
        INSERT INTO transcode_plans (name, profile_id, status, created_at, updated_at, notes)
        VALUES (?, ?, 'draft', ?, ?, ?)
        """,
        (name, profile_id, now, now, notes),
    )
    rows = []
    for file_row in files:
        target_path = plan_target_path(file_row, profile)
        command = build_command(profile, file_row["path"], target_path)
        warnings = transcode_warnings(file_row)
        reason = file_row.get("recommendation_summary") or "Selected by user."
        rows.append(
            (
                plan_id,
                file_row["id"],
                file_row["path"],
                target_path,
                profile["command_template"],
                reason,
                db.dumps(command) if command else None,
                safe_shell_join(command) if command else None,
                db.dumps(warnings),
            )
        )
    db.executemany(
        """
        INSERT INTO transcode_plan_items (
            plan_id, file_id, source_path, target_path, action, reason,
            command_json, command_display, warnings_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return get_plan(plan_id)


def get_plan(plan_id: int) -> dict[str, Any]:
    plan = db.query_one(
        """
        SELECT tp.*, p.name AS profile_name
        FROM transcode_plans tp
        LEFT JOIN transcode_profiles p ON p.id = tp.profile_id
        WHERE tp.id = ?
        """,
        (plan_id,),
    )
    if not plan:
        raise ValueError("Transcode plan not found.")
    plan["items"] = db.query_all("SELECT * FROM transcode_plan_items WHERE plan_id = ? ORDER BY id", (plan_id,))
    return plan
