from __future__ import annotations

import asyncio
import errno
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .. import db
from ..config import CONFIG
from .ffprobe import ProbeError, probe_file
from .paths import safe_shell_join
from .recommendations import transcode_warnings


def transcode_savings_stats() -> dict[str, Any]:
    runs = db.query_all("SELECT * FROM transcode_runs")
    items = db.query_all(
        """
        SELECT tri.*, f.size_bytes AS file_size_bytes
        FROM transcode_run_items tri
        LEFT JOIN files f ON f.id = tri.file_id
        """
    )
    total_source_size = 0
    total_output_size = 0
    measured_items = 0
    item_runtime_seconds = 0.0
    now = db.utc_now()

    for item in items:
        runtime_end = item.get("finished_at") or (now if item.get("status") == "running" else None)
        item_runtime_seconds += _duration_seconds(item.get("started_at"), runtime_end)
        if item.get("status") != "succeeded":
            continue
        source_size = item.get("source_size_bytes")
        if source_size is None:
            source_size = item.get("file_size_bytes")
        output_size = item.get("output_size_bytes")
        if output_size is None:
            target = Path(item["target_path"])
            if _is_within(target, CONFIG.transcoder.staging_dir) and target.exists() and target.is_file():
                output_size = target.stat().st_size
        if source_size is None or output_size is None:
            continue
        measured_items += 1
        total_source_size += int(source_size)
        total_output_size += int(output_size)

    saved = total_source_size - total_output_size
    return {
        "runs_total": len(runs),
        "runs_started": sum(1 for run in runs if run.get("started_at")),
        "runs_succeeded": sum(1 for run in runs if run.get("status") == "succeeded"),
        "runs_archived": sum(1 for run in runs if run.get("archived_at")),
        "items_total": len(items),
        "items_succeeded": sum(1 for item in items if item.get("status") == "succeeded"),
        "items_published": sum(1 for item in items if item.get("published_at")),
        "items_cleaned": sum(1 for item in items if item.get("cleanup_status") == "cleaned"),
        "items_with_size_comparison": measured_items,
        "total_runtime_seconds": round(item_runtime_seconds, 2),
        "total_source_size_bytes": total_source_size,
        "total_output_size_bytes": total_output_size,
        "total_space_saved_bytes": saved,
        "savings_percent": round((saved / total_source_size) * 100, 2) if total_source_size else 0,
    }


def _duration_seconds(start: str | None, end: str | None) -> float:
    if not start or not end:
        return 0.0
    try:
        start_dt = datetime.fromisoformat(start)
        end_dt = datetime.fromisoformat(end)
    except ValueError:
        return 0.0
    return max(0.0, (end_dt - start_dt).total_seconds())


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

    async def recover_startup_jobs(self) -> None:
        db.mark_running_transcodes_interrupted()
        db.execute(
            """
            UPDATE transcode_run_items
            SET publish_status = 'interrupted',
                publish_step = 'interrupted',
                publish_message = ?,
                publish_finished_at = ?
            WHERE publish_status IN ('queued', 'running')
            """,
            (
                "Backend restarted while publish was active. Inspect the original file, staged output, and backup before retrying.",
                db.utc_now(),
            ),
        )
        queued = db.query_one(
            """
            SELECT id
            FROM transcode_run_items
            WHERE status = 'queued'
            ORDER BY id
            LIMIT 1
            """
        )
        if queued:
            await self.ensure_worker()

    async def create_run(self, plan_id: int, name: str | None = None) -> dict[str, Any]:
        plan = db.query_one("SELECT * FROM transcode_plans WHERE id = ?", (plan_id,))
        if not plan:
            raise ValueError("Transcode plan not found.")
        if plan.get("archived_at"):
            raise ValueError("Archived transcode plans cannot be started. Unarchive the plan first.")
        items = db.query_all("SELECT * FROM transcode_plan_items WHERE plan_id = ? ORDER BY id", (plan_id,))
        runnable = [item for item in items if item.get("command_json")]
        if not runnable:
            raise ValueError("Plan has no runnable items.")
        file_sizes = {
            row["id"]: row["size_bytes"]
            for row in db.query_all(
                f"""
                SELECT id, size_bytes
                FROM files
                WHERE id IN ({",".join("?" for _ in runnable)})
                """,
                tuple(item["file_id"] for item in runnable),
            )
        }
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
                    file_sizes.get(item["file_id"]),
                    now,
                )
            )
        db.executemany(
            """
            INSERT INTO transcode_run_items (
                run_id, plan_item_id, file_id, status, source_path, target_path,
                command_json, command_display, warnings_json, source_size_bytes, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    def cleanup_run_artifacts(
        self,
        run_id: int,
        confirmation_text: str,
        archive_run: bool = True,
    ) -> dict[str, Any]:
        if confirmation_text != "DELETE ARTIFACTS":
            raise ValueError("Cleanup requires the confirmation text DELETE ARTIFACTS.")
        run = db.query_one("SELECT * FROM transcode_runs WHERE id = ?", (run_id,))
        if not run:
            raise ValueError("Transcode run not found.")
        if run["status"] in {"queued", "running"}:
            raise ValueError("Active transcode runs cannot be cleaned up.")

        items = db.query_all("SELECT * FROM transcode_run_items WHERE run_id = ? ORDER BY id", (run_id,))
        published_items = [item for item in items if item.get("published_at")]
        if not published_items:
            raise ValueError("No published transcode items are available for cleanup.")

        summary: dict[str, Any] = {
            "items_seen": len(published_items),
            "items_cleaned": 0,
            "items_skipped": 0,
            "errors": [],
            "staged_deleted": 0,
            "backups_deleted": 0,
            "run_archived": False,
        }

        for item in published_items:
            result = self._cleanup_item_artifacts(item)
            if result["skipped"]:
                summary["items_skipped"] += 1
                continue
            if result["staged_deleted"]:
                summary["staged_deleted"] += 1
            if result["backup_deleted"]:
                summary["backups_deleted"] += 1
            if result["errors"]:
                summary["errors"].append({"item_id": item["id"], "message": result["message"]})
                continue
            summary["items_cleaned"] += 1

        updated_items = db.query_all("SELECT * FROM transcode_run_items WHERE run_id = ? ORDER BY id", (run_id,))
        all_items_cleaned = all(item.get("published_at") and item.get("cleanup_status") == "cleaned" for item in updated_items)

        if archive_run and not summary["errors"] and all_items_cleaned:
            archived_at = db.utc_now()
            db.execute(
                """
                UPDATE transcode_runs
                SET archived_at = COALESCE(archived_at, ?),
                    message = ?
                WHERE id = ?
                """,
                (archived_at, "Cleanup completed and run archived.", run_id),
            )
            summary["run_archived"] = True
        elif archive_run and not summary["errors"]:
            db.execute(
                """
                UPDATE transcode_runs
                SET message = ?
                WHERE id = ?
                """,
                ("Cleanup completed for published items. Run was not archived because some items are not published and cleaned.", run_id),
            )

        updated = db.query_one("SELECT * FROM transcode_runs WHERE id = ?", (run_id,)) or run
        updated["items"] = db.query_all("SELECT * FROM transcode_run_items WHERE run_id = ? ORDER BY id", (run_id,))
        updated["cleanup_summary"] = summary
        return updated

    def cleanup_item_artifacts(
        self,
        run_id: int,
        item_id: int,
        confirmation_text: str,
    ) -> dict[str, Any]:
        if confirmation_text != "DELETE ARTIFACTS":
            raise ValueError("Cleanup requires the confirmation text DELETE ARTIFACTS.")
        run = db.query_one("SELECT * FROM transcode_runs WHERE id = ?", (run_id,))
        if not run:
            raise ValueError("Transcode run not found.")
        if run["status"] in {"queued", "running"}:
            raise ValueError("Active transcode run items cannot be cleaned up.")
        item = db.query_one("SELECT * FROM transcode_run_items WHERE id = ? AND run_id = ?", (item_id, run_id))
        if not item:
            raise ValueError("Transcode run item not found.")
        if not item.get("published_at"):
            raise ValueError("Only published transcode items can be cleaned up.")
        self._cleanup_item_artifacts(item)
        return db.query_one("SELECT * FROM transcode_run_items WHERE id = ? AND run_id = ?", (item_id, run_id)) or item

    def _cleanup_item_artifacts(self, item: dict[str, Any]) -> dict[str, Any]:
        if item.get("cleanup_status") == "cleaned":
            return {
                "skipped": True,
                "staged_deleted": False,
                "backup_deleted": False,
                "errors": [],
                "message": item.get("cleanup_message") or "Item already cleaned.",
            }
        item_id = item["id"]
        started_at = db.utc_now()
        db.execute(
            """
            UPDATE transcode_run_items
            SET cleanup_status = 'running',
                cleanup_message = 'Cleaning up staged and backup artifacts.',
                cleanup_started_at = COALESCE(cleanup_started_at, ?),
                cleanup_finished_at = NULL
            WHERE id = ?
            """,
            (started_at, item_id),
        )
        messages: list[str] = []
        errors: list[str] = []
        staged_deleted_at = None
        backup_deleted_at = None
        staged_deleted = False
        backup_deleted = False

        try:
            deleted, message = _delete_artifact(Path(item["target_path"]), CONFIG.transcoder.staging_dir)
            messages.append(f"Staged output: {message}")
            if deleted:
                staged_deleted = True
                staged_deleted_at = db.utc_now()
        except ValueError as exc:
            errors.append(str(exc))

        backup_path = item.get("published_backup_path")
        if backup_path:
            try:
                deleted, message = _delete_artifact(Path(backup_path), CONFIG.transcoder.backup_dir)
                messages.append(f"Backup: {message}")
                if deleted:
                    backup_deleted = True
                    backup_deleted_at = db.utc_now()
            except ValueError as exc:
                errors.append(str(exc))
        else:
            messages.append("Backup: no backup path was recorded.")

        finished_at = db.utc_now()
        if errors:
            message = "; ".join(errors + messages)
            db.execute(
                """
                UPDATE transcode_run_items
                SET cleanup_status = 'failed',
                    cleanup_message = ?,
                    cleanup_finished_at = ?,
                    staged_deleted_at = COALESCE(staged_deleted_at, ?),
                    backup_deleted_at = COALESCE(backup_deleted_at, ?)
                WHERE id = ?
                """,
                (message, finished_at, staged_deleted_at, backup_deleted_at, item_id),
            )
            return {
                "skipped": False,
                "staged_deleted": staged_deleted,
                "backup_deleted": backup_deleted,
                "errors": errors,
                "message": message,
            }

        message = "; ".join(messages)
        db.execute(
            """
            UPDATE transcode_run_items
            SET cleanup_status = 'cleaned',
                cleanup_message = ?,
                cleanup_finished_at = ?,
                staged_deleted_at = COALESCE(staged_deleted_at, ?),
                backup_deleted_at = COALESCE(backup_deleted_at, ?)
            WHERE id = ?
            """,
            (message, finished_at, staged_deleted_at, backup_deleted_at, item_id),
        )
        return {
            "skipped": False,
            "staged_deleted": staged_deleted,
            "backup_deleted": backup_deleted,
            "errors": [],
            "message": message,
        }

    async def retry_run(self, run_id: int) -> dict[str, Any]:
        now = db.utc_now()
        retryable_items = db.query_all(
            """
            SELECT *
            FROM transcode_run_items
            WHERE run_id = ? AND status IN ('failed', 'interrupted', 'verification_failed', 'canceled')
            """,
            (run_id,),
        )
        for item in retryable_items:
            self._quarantine_target(item)
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
                output_size_bytes = NULL,
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

    def publish_item(
        self,
        run_id: int,
        item_id: int,
        source_path: str,
        target_path: str,
        confirmation_text: str,
    ) -> dict[str, Any]:
        if confirmation_text != "REPLACE":
            raise ValueError("Publish requires the confirmation text REPLACE.")
        item = db.query_one("SELECT * FROM transcode_run_items WHERE id = ? AND run_id = ?", (item_id, run_id))
        if not item:
            raise ValueError("Transcode run item not found.")
        if item.get("published_at"):
            raise ValueError("This transcode item has already been published.")
        if item.get("publish_status") in {"queued", "running"}:
            raise ValueError("This transcode item is already being published.")
        if item["status"] != "succeeded" or item.get("verification_status") != "verified":
            raise ValueError("Only verified, successful transcode items can be published.")
        if source_path != item["source_path"] or target_path != item["target_path"]:
            raise ValueError("Submitted source or staged output path does not match the run item.")

        source = Path(item["source_path"])
        target = Path(item["target_path"])
        now = db.utc_now()
        db.execute(
            """
            UPDATE transcode_run_items
            SET publish_status = 'running',
                publish_message = 'Validating publish inputs.',
                publish_step = 'validating',
                publish_started_at = COALESCE(publish_started_at, ?),
                publish_finished_at = NULL,
                publish_progress_percent = 0,
                publish_bytes_done = 0,
                publish_bytes_total = 0
            WHERE id = ?
            """,
            (now, item_id),
        )
        if not _is_within(target, CONFIG.transcoder.staging_dir):
            self._store_publish_failure(item_id, "Staged output path is outside the configured transcode staging directory.")
            raise ValueError("Staged output path is outside the configured transcode staging directory.")
        if not source.exists() or not source.is_file():
            self._store_publish_failure(item_id, "Original source file is missing.")
            raise ValueError("Original source file is missing.")
        if not target.exists() or not target.is_file() or target.stat().st_size == 0:
            self._store_publish_failure(item_id, "Verified staged output is missing or empty.")
            raise ValueError("Verified staged output is missing or empty.")
        if source.resolve() == target.resolve():
            self._store_publish_failure(item_id, "Source and staged output paths must be different.")
            raise ValueError("Source and staged output paths must be different.")
        if not os.access(source, os.R_OK) or not os.access(source.parent, os.W_OK):
            self._store_publish_failure(
                item_id,
                "Original source file is not readable or its directory is not writable. Check the media mount mode.",
            )
            raise ValueError("Original source file is not readable or its directory is not writable. Check the media mount mode.")
        try:
            CONFIG.transcoder.backup_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._store_publish_failure(item_id, f"Transcode backup directory is not writable: {exc}")
            raise ValueError(f"Transcode backup directory is not writable: {exc}") from exc
        if not os.access(CONFIG.transcoder.backup_dir, os.W_OK):
            self._store_publish_failure(item_id, "Transcode backup directory is not writable.")
            raise ValueError("Transcode backup directory is not writable.")

        backup_path = _publish_backup_path(source, run_id, item_id)
        temp_path = _publish_temp_path(source, run_id, item_id)
        source_size = source.stat().st_size
        target_size = target.stat().st_size
        total_bytes = max(1, source_size + target_size)
        self._store_publish_progress(
            item_id,
            "copying_staged_output",
            "Copying staged output into a temporary file beside the original.",
            0,
            total_bytes,
        )
        try:
            _copy_file_with_progress(
                target,
                temp_path,
                0,
                total_bytes,
                lambda done: self._store_publish_progress(
                    item_id,
                    "copying_staged_output",
                    "Copying staged output into a temporary file beside the original.",
                    done,
                    total_bytes,
                ),
            )
            self._store_publish_progress(
                item_id,
                "moving_original_to_backup",
                "Moving original file into transcode backup storage.",
                target_size,
                total_bytes,
            )
            _move_file(
                source,
                backup_path,
                target_size,
                total_bytes,
                lambda done: self._store_publish_progress(
                    item_id,
                    "moving_original_to_backup",
                    "Moving original file into transcode backup storage.",
                    done,
                    total_bytes,
                ),
            )
            try:
                self._store_publish_progress(
                    item_id,
                    "replacing_original",
                    "Replacing original path with staged output.",
                    total_bytes,
                    total_bytes,
                )
                temp_path.replace(source)
            except OSError:
                if backup_path.exists() and not source.exists():
                    self._store_publish_progress(
                        item_id,
                        "rolling_back",
                        "Replacement failed; moving backup back to the original path.",
                        0,
                        max(1, source_size),
                    )
                    _move_file(
                        backup_path,
                        source,
                        0,
                        max(1, source_size),
                        lambda done: self._store_publish_progress(
                            item_id,
                            "rolling_back",
                            "Replacement failed; moving backup back to the original path.",
                            done,
                            max(1, source_size),
                        ),
                    )
                raise
        except OSError as exc:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            message = f"Publish failed: {exc}"
            self._store_publish_failure(item_id, message)
            raise ValueError(message) from exc

        finished_at = db.utc_now()
        db.execute(
            """
            UPDATE transcode_run_items
            SET published_at = ?,
                publish_status = 'published',
                publish_message = ?,
                publish_step = 'completed',
                publish_finished_at = ?,
                publish_progress_percent = 100,
                publish_bytes_done = ?,
                publish_bytes_total = ?,
                source_size_bytes = COALESCE(source_size_bytes, ?),
                output_size_bytes = COALESCE(output_size_bytes, ?),
                published_backup_path = ?
            WHERE id = ?
            """,
            (
                finished_at,
                "Published staged output to original source path. Original file was moved to transcode backup storage.",
                finished_at,
                total_bytes,
                total_bytes,
                source_size,
                target_size,
                str(backup_path),
                item_id,
            ),
        )
        return db.query_one("SELECT * FROM transcode_run_items WHERE id = ? AND run_id = ?", (item_id, run_id)) or item

    def _store_publish_progress(
        self,
        item_id: int,
        step: str,
        message: str,
        bytes_done: int,
        bytes_total: int,
    ) -> None:
        percent = min(99.0, max(0.0, (bytes_done / max(1, bytes_total)) * 100))
        db.execute(
            """
            UPDATE transcode_run_items
            SET publish_status = 'running',
                publish_step = ?,
                publish_message = ?,
                publish_progress_percent = ?,
                publish_bytes_done = ?,
                publish_bytes_total = ?
            WHERE id = ?
            """,
            (step, message, percent, bytes_done, bytes_total, item_id),
        )

    def _store_publish_failure(self, item_id: int, message: str) -> None:
        db.execute(
            """
            UPDATE transcode_run_items
            SET publish_status = 'failed',
                publish_step = 'failed',
                publish_message = ?,
                publish_finished_at = ?
            WHERE id = ?
            """,
            (message, db.utc_now(), item_id),
        )

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
            preflight_errors = self._preflight_item(item, command)
            if preflight_errors:
                with log_path.open("a", encoding="utf-8") as log_file:
                    log_file.write(f"[{now}] Preflight failed:\n")
                    for error in preflight_errors:
                        log_file.write(f"- {error}\n")
                self._finish_item(item["id"], "failed", None, "not_run", "; ".join(preflight_errors))
                return
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

    def _preflight_item(self, item: dict[str, Any], command: list[Any]) -> list[str]:
        errors: list[str] = []
        source = Path(item["source_path"])
        target = Path(item["target_path"])
        if not isinstance(command, list) or not command or any(not isinstance(part, str) for part in command):
            errors.append("Stored transcode command is invalid.")
            return errors
        if command[0] != CONFIG.transcoder.ffmpeg_path:
            errors.append("Stored command does not use the configured ffmpeg executable.")
        if item["source_path"] not in command:
            errors.append("Stored command does not reference the expected source path.")
        if command[-1] != item["target_path"]:
            errors.append("Stored command does not write to the expected staged target path.")
        if ("hevc_qsv" in command or "hevc_vaapi" in command) and not Path("/dev/dri/renderD128").exists():
            errors.append("Hardware encoder requires /dev/dri/renderD128 inside the container.")
        if not source.exists() or not source.is_file():
            errors.append("Source file is missing.")
        elif not os.access(source, os.R_OK):
            errors.append("Source file is not readable.")
        target_within_staging = _is_within(target, CONFIG.transcoder.staging_dir)
        if not target_within_staging:
            errors.append("Target path is outside the configured transcode staging directory.")
        if target.exists():
            errors.append("Target path already exists; retry will not overwrite staged outputs.")
        if target_within_staging:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                errors.append(f"Could not create target directory: {exc}")
            if not os.access(target.parent, os.W_OK):
                errors.append("Target directory is not writable.")
            try:
                usage = shutil.disk_usage(target.parent)
                source_size = source.stat().st_size if source.exists() else 0
                required = max(CONFIG.transcoder.min_free_bytes, min(source_size, CONFIG.transcoder.min_free_bytes * 4))
                if usage.free < required:
                    errors.append(
                        f"Transcode staging filesystem has {usage.free} bytes free; at least {required} bytes are required."
                    )
            except OSError as exc:
                errors.append(f"Could not check free disk space: {exc}")
        return errors

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
        item = db.query_one("SELECT target_path, source_path, source_size_bytes FROM transcode_run_items WHERE id = ?", (item_id,))
        source_size = item.get("source_size_bytes") if item else None
        if item and source_size is None:
            source = Path(item["source_path"])
            if source.exists() and source.is_file():
                source_size = source.stat().st_size
        output_size = None
        if item and status == "succeeded":
            target = Path(item["target_path"])
            if target.exists() and target.is_file():
                output_size = target.stat().st_size
        db.execute(
            """
            UPDATE transcode_run_items
            SET status = ?,
                progress_percent = CASE WHEN ? = 'succeeded' THEN 100 ELSE progress_percent END,
                exit_code = ?,
                verification_status = ?,
                verification_message = ?,
                source_size_bytes = COALESCE(source_size_bytes, ?),
                output_size_bytes = COALESCE(?, output_size_bytes),
                finished_at = ?
            WHERE id = ?
            """,
            (
                status,
                status,
                exit_code,
                verification_status,
                verification_message,
                source_size,
                output_size,
                db.utc_now(),
                item_id,
            ),
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

    def _quarantine_target(self, item: dict[str, Any]) -> None:
        target = Path(item["target_path"])
        if not target.exists() or not _is_within(target, CONFIG.transcoder.staging_dir):
            return
        quarantine_dir = CONFIG.transcoder.staging_dir / ".quarantine" / f"run-{item['run_id']}"
        quarantine_dir.mkdir(parents=True, exist_ok=True)
        suffix = db.utc_now().replace(":", "").replace("+", "Z")
        destination = quarantine_dir / f"item-{item['id']}-{target.name}.{suffix}.partial"
        target.replace(destination)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.expanduser().resolve().relative_to(root.expanduser().resolve())
        return True
    except ValueError:
        return False


def _publish_backup_path(source: Path, run_id: int, item_id: int) -> Path:
    suffix = db.utc_now().replace(":", "").replace("+", "Z")
    backup_dir = CONFIG.transcoder.backup_dir / f"run-{run_id}" / f"item-{item_id}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    base_name = f"{source.name}.media-atlas-backup-{suffix}"
    candidate = backup_dir / base_name
    for index in range(1, 10_000):
        if not candidate.exists():
            return candidate
        candidate = backup_dir / f"{base_name}-{index}"
    raise ValueError(f"Could not find available backup path for {source}")


def _publish_temp_path(source: Path, run_id: int, item_id: int) -> Path:
    base_name = f".{source.name}.media-atlas-publish-run-{run_id}-item-{item_id}.tmp"
    candidate = source.with_name(base_name)
    for index in range(1, 10_000):
        if not candidate.exists():
            return candidate
        candidate = source.with_name(f"{base_name}-{index}")
    raise ValueError(f"Could not find available temporary publish path for {source}")


ProgressCallback = Callable[[int], None]


def _copy_file_with_progress(
    source: Path,
    target: Path,
    base_bytes_done: int,
    bytes_total: int,
    progress: ProgressCallback,
) -> None:
    copied = 0
    buffer_size = 4 * 1024 * 1024
    report_interval = 16 * 1024 * 1024
    next_report = report_interval
    with source.open("rb") as source_file:
        with target.open("wb") as target_file:
            while True:
                chunk = source_file.read(buffer_size)
                if not chunk:
                    break
                target_file.write(chunk)
                copied += len(chunk)
                if copied >= next_report:
                    progress(min(bytes_total, base_bytes_done + copied))
                    next_report += report_interval
    shutil.copystat(source, target)
    progress(min(bytes_total, base_bytes_done + source.stat().st_size))


def _move_file(
    source: Path,
    target: Path,
    base_bytes_done: int,
    bytes_total: int,
    progress: ProgressCallback,
) -> None:
    try:
        source.replace(target)
        progress(min(bytes_total, base_bytes_done + target.stat().st_size))
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        _copy_file_with_progress(source, target, base_bytes_done, bytes_total, progress)
        try:
            source.unlink()
        except OSError:
            target.unlink(missing_ok=True)
            raise


def _delete_artifact(path: Path, root: Path) -> tuple[bool, str]:
    if not _is_within(path, root):
        raise ValueError(f"Refusing to delete artifact outside {root}: {path}")
    if not path.exists():
        return False, "already absent."
    if not path.is_file():
        raise ValueError(f"Refusing to delete non-file artifact: {path}")
    path.unlink()
    _prune_empty_parents(path.parent, root)
    return True, "deleted."


def _prune_empty_parents(path: Path, root: Path) -> None:
    root = root.expanduser().resolve()
    current = path.expanduser().resolve()
    while current != root:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def build_command(profile: dict[str, Any], source_path: str, target_path: str) -> list[str] | None:
    template = profile["command_template"]
    ffmpeg = CONFIG.transcoder.ffmpeg_path
    if template == "manual_review":
        return None
    if template == "remux_mkv":
        return [ffmpeg, "-n", "-i", source_path, "-map", "0", "-c", "copy", target_path]
    if template == "hevc_archive":
        return _hevc_software_command(ffmpeg, source_path, target_path, crf="20", preset="medium")
    if template == "hevc_archive_fast":
        return _hevc_software_command(ffmpeg, source_path, target_path, crf="21", preset="fast")
    if template == "hevc_archive_faster":
        return _hevc_software_command(ffmpeg, source_path, target_path, crf="22", preset="faster")
    if template == "hevc_qsv":
        return [
            ffmpeg,
            "-n",
            "-i",
            source_path,
            "-map",
            "0",
            "-c:v",
            "hevc_qsv",
            "-global_quality",
            "24",
            "-c:a",
            "copy",
            "-c:s",
            "copy",
            target_path,
        ]
    if template == "hevc_nvenc":
        return [
            ffmpeg,
            "-n",
            "-i",
            source_path,
            "-map",
            "0",
            "-c:v",
            "hevc_nvenc",
            "-cq",
            "24",
            "-preset",
            "p5",
            "-c:a",
            "copy",
            "-c:s",
            "copy",
            target_path,
        ]
    if template == "hevc_vaapi":
        return [
            ffmpeg,
            "-n",
            "-vaapi_device",
            "/dev/dri/renderD128",
            "-i",
            source_path,
            "-map",
            "0",
            "-vf",
            "format=nv12,hwupload",
            "-c:v",
            "hevc_vaapi",
            "-qp",
            "24",
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


def _hevc_software_command(
    ffmpeg: str,
    source_path: str,
    target_path: str,
    *,
    crf: str,
    preset: str,
) -> list[str]:
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
        crf,
        "-preset",
        preset,
        "-c:a",
        "copy",
        "-c:s",
        "copy",
        target_path,
    ]


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
