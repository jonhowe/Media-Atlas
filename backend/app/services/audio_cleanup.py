from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

from .. import db
from ..config import CONFIG
from .ffprobe import ProbeError, probe_file
from .metadata import normalize_probe
from .paths import safe_shell_join
from .recommendations import recommend


class AudioCleanupError(ValueError):
    pass


def _tool_available(command: str) -> bool:
    return bool(command and (Path(command).is_file() if Path(command).is_absolute() else shutil.which(command)))


def _text(value: Any) -> str | None:
    if value in (None, "", "und", "unknown"):
        return None
    return str(value)


def _int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "", "N/A") else None
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "", "N/A") else None
    except (TypeError, ValueError):
        return None


def _stream_signature(stream: dict[str, Any]) -> dict[str, Any]:
    tags = stream.get("tags") or {}
    disposition = stream.get("disposition") or {}
    return {
        "stream_type": stream.get("codec_type"),
        "codec_name": stream.get("codec_name"),
        "profile": _text(stream.get("profile")),
        "language": _text(tags.get("language")),
        "title": _text(tags.get("title")),
        "channels": _int(stream.get("channels")),
        "channel_layout": _text(stream.get("channel_layout")),
        "sample_rate": _int(stream.get("sample_rate")),
        "width": _int(stream.get("width")),
        "height": _int(stream.get("height")),
        "pixel_format": _text(stream.get("pix_fmt")),
        "disposition_default": bool(disposition.get("default")),
        "disposition_forced": bool(disposition.get("forced")),
        "attachment_filename": _text(tags.get("filename")),
        "attachment_mimetype": _text(tags.get("mimetype")),
    }


def _chapter_signature(chapter: dict[str, Any]) -> dict[str, Any]:
    return {
        "start": _float(chapter.get("start_time")),
        "end": _float(chapter.get("end_time")),
        "title": _text((chapter.get("tags") or {}).get("title")),
    }


def _duration(raw: dict[str, Any]) -> float | None:
    return _float((raw.get("format") or {}).get("duration"))


def _fingerprint(path: Path, raw: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    stat_result = path.stat()
    snapshot = {
        "path": str(path.resolve()),
        "size_bytes": stat_result.st_size,
        "modified_time_ns": stat_result.st_mtime_ns,
        "duration_seconds": _duration(raw),
        "streams": [_stream_signature(stream) for stream in raw.get("streams") or []],
        "chapters": [_chapter_signature(chapter) for chapter in raw.get("chapters") or []],
    }
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), snapshot


def _is_matroska(raw: dict[str, Any]) -> bool:
    names = str((raw.get("format") or {}).get("format_name") or "").lower().split(",")
    return "matroska" in names or "webm" in names


async def _identify_mkvmerge(path: Path) -> dict[str, Any]:
    if not _tool_available(CONFIG.transcoder.mkvmerge_path):
        raise AudioCleanupError("MKVToolNix is not available; configure MEDIA_ATLAS_MKVMERGE_PATH.")
    proc = await asyncio.create_subprocess_exec(
        CONFIG.transcoder.mkvmerge_path,
        "-J",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=CONFIG.scanner.timeout_seconds
        )
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise AudioCleanupError("MKVToolNix identification timed out.") from exc
    if proc.returncode != 0:
        message = stderr.decode("utf-8", "replace").strip() or "MKVToolNix identification failed."
        raise AudioCleanupError(message)
    try:
        return json.loads(stdout.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise AudioCleanupError("MKVToolNix returned invalid identification JSON.") from exc


def _safe_segment(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in value)
    return cleaned.strip("._") or "media"


def _dedupe_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(1, 10_000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise AudioCleanupError(f"Could not find an available staged path for {path.name}.")


def _paths_for_file(file_row: dict[str, Any]) -> tuple[Path, Path]:
    source = Path(file_row["path"]).resolve()
    root = db.query_one("SELECT * FROM media_roots WHERE id = ?", (file_row["root_id"],)) or {}
    try:
        relative = source.relative_to(Path(root.get("path") or source.parent).resolve())
    except ValueError:
        relative = Path(source.name)
    staged_relative = relative.with_name(f"{relative.stem}.audio-cleanup.mkv")
    staged = _dedupe_path(
        CONFIG.transcoder.staging_dir / _safe_segment(root.get("name") or "media") / staged_relative
    )
    publish = source if source.suffix.lower() == ".mkv" else source.with_suffix(".mkv")
    return staged, publish


def _track_payload(
    stream: dict[str, Any],
    audio_ordinal: int,
    backend: str,
    tool_track_id: int,
) -> dict[str, Any]:
    tags = stream.get("tags") or {}
    disposition = stream.get("disposition") or {}
    stream_index = int(stream.get("index", audio_ordinal))
    return {
        "selection_key": f"{backend}:{tool_track_id}",
        "stream_index": stream_index,
        "audio_ordinal": audio_ordinal,
        "tool_track_id": tool_track_id,
        "codec_name": stream.get("codec_name"),
        "codec_long_name": stream.get("codec_long_name"),
        "profile": stream.get("profile"),
        "language": _text(tags.get("language")),
        "title": _text(tags.get("title")),
        "channels": _int(stream.get("channels")),
        "channel_layout": _text(stream.get("channel_layout")),
        "sample_rate": _int(stream.get("sample_rate")),
        "bit_rate": _int(stream.get("bit_rate")),
        "default": bool(disposition.get("default")),
        "forced": bool(disposition.get("forced")),
        "signature": _stream_signature(stream),
    }


async def inspect_file(file_id: int) -> dict[str, Any]:
    file_row = db.query_one("SELECT * FROM files WHERE id = ?", (file_id,))
    if not file_row:
        raise AudioCleanupError("Media file not found.")
    source = Path(file_row["path"])
    blockers: list[str] = []
    warnings: list[str] = []
    if file_row.get("is_missing"):
        blockers.append("The inventory marks this file as missing.")
    if not source.exists() or not source.is_file():
        blockers.append("The source file is missing.")
    if blockers:
        return {
            "file_id": file_id,
            "filename": file_row["filename"],
            "source_path": file_row["path"],
            "audio_tracks": [],
            "blockers": blockers,
            "warnings": warnings,
        }
    try:
        raw = await probe_file(source)
    except ProbeError as exc:
        raise AudioCleanupError(f"Fresh ffprobe inspection failed: {exc.stderr}") from exc

    raw_audio = [stream for stream in raw.get("streams") or [] if stream.get("codec_type") == "audio"]
    backend = "mkvmerge" if _is_matroska(raw) else "ffmpeg"
    tool_ids: list[int] = []
    if backend == "mkvmerge":
        try:
            identified = await _identify_mkvmerge(source)
            tool_ids = [
                int(track["id"])
                for track in identified.get("tracks") or []
                if track.get("type") == "audio"
            ]
            if len(tool_ids) != len(raw_audio):
                blockers.append(
                    "MKVToolNix and ffprobe reported different audio-track counts; selection is unsafe."
                )
        except AudioCleanupError as exc:
            blockers.append(str(exc))
    else:
        if not _tool_available(CONFIG.transcoder.ffmpeg_path):
            blockers.append("FFmpeg is not available.")
        tool_ids = [int(stream.get("index", ordinal)) for ordinal, stream in enumerate(raw_audio)]

    tracks = [
        _track_payload(stream, ordinal, backend, tool_ids[ordinal])
        for ordinal, stream in enumerate(raw_audio)
        if ordinal < len(tool_ids)
    ]
    if len(raw_audio) < 2:
        blockers.append("Audio cleanup requires at least two audio tracks.")

    staged, publish = _paths_for_file(file_row)
    if publish != source.resolve() and publish.exists():
        blockers.append(f"The live MKV destination already exists: {publish}")
    destination_row = db.query_one("SELECT id FROM files WHERE path = ? AND id != ?", (str(publish), file_id))
    if destination_row:
        blockers.append("Another inventory row already uses the planned live MKV destination.")
    if publish != source.resolve():
        warnings.append(
            f"Publishing will replace {source.name} with {publish.name}; Plex and the owning Arr service must rescan the path."
        )
    fingerprint, snapshot = _fingerprint(source.resolve(), raw)
    return {
        "file_id": file_id,
        "filename": file_row["filename"],
        "source_path": str(source.resolve()),
        "staged_path": str(staged),
        "publish_path": str(publish),
        "backend": backend,
        "output_container": "mkv",
        "source_fingerprint": fingerprint,
        "audio_tracks": tracks,
        "non_audio_stream_count": len(raw.get("streams") or []) - len(raw_audio),
        "chapter_count": len(raw.get("chapters") or []),
        "blockers": blockers,
        "warnings": warnings,
        "source_snapshot": snapshot,
    }


def public_inspection(inspection: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in inspection.items() if key != "source_snapshot"}


def build_operation(
    inspection: dict[str, Any],
    removed_selection_keys: list[str],
    replacement_default_key: str | None,
) -> dict[str, Any]:
    if inspection.get("blockers"):
        raise AudioCleanupError("; ".join(inspection["blockers"]))
    tracks = inspection["audio_tracks"]
    by_key = {track["selection_key"]: track for track in tracks}
    removed_keys = list(dict.fromkeys(removed_selection_keys))
    if not removed_keys:
        raise AudioCleanupError("Select at least one audio track to remove.")
    unknown = [key for key in removed_keys if key not in by_key]
    if unknown:
        raise AudioCleanupError("One or more selected audio tracks are no longer available.")
    removed = [by_key[key] for key in removed_keys]
    retained = [track for track in tracks if track["selection_key"] not in removed_keys]
    if not retained:
        raise AudioCleanupError("At least one audio track must be retained.")

    removed_default = any(track["default"] for track in removed)
    if removed_default:
        if not replacement_default_key or replacement_default_key not in {
            track["selection_key"] for track in retained
        }:
            raise AudioCleanupError("Choose a retained replacement default audio track.")
    elif replacement_default_key:
        raise AudioCleanupError("A replacement default is only used when removing a default track.")

    target = inspection["staged_path"]
    source = inspection["source_path"]
    backend = inspection["backend"]
    if backend == "mkvmerge":
        removed_ids = ",".join(str(track["tool_track_id"]) for track in removed)
        command = [
            CONFIG.transcoder.mkvmerge_path,
            "--gui-mode",
            "--output",
            target,
            "--audio-tracks",
            f"!{removed_ids}",
        ]
        if removed_default:
            for track in retained:
                command.extend(
                    [
                        "--default-track-flag",
                        f"{track['tool_track_id']}:{1 if track['selection_key'] == replacement_default_key else 0}",
                    ]
                )
        command.append(source)
    else:
        command = [CONFIG.transcoder.ffmpeg_path, "-n", "-i", source, "-map", "0"]
        for track in removed:
            command.extend(["-map", f"-0:{track['stream_index']}"])
        command.extend(["-map_metadata", "0", "-map_chapters", "0", "-c", "copy"])
        if removed_default:
            for output_ordinal, track in enumerate(retained):
                command.extend(
                    [
                        f"-disposition:a:{output_ordinal}",
                        "default" if track["selection_key"] == replacement_default_key else "0",
                    ]
                )
        command.append(target)

    expected_audio: list[dict[str, Any]] = []
    for track in retained:
        signature = dict(track["signature"])
        if removed_default:
            signature["disposition_default"] = track["selection_key"] == replacement_default_key
        expected_audio.append(signature)
    source_snapshot = inspection["source_snapshot"]
    return {
        "version": 1,
        "operation_type": "audio_cleanup",
        "backend": backend,
        "source_fingerprint": inspection["source_fingerprint"],
        "source_snapshot": source_snapshot,
        "removed_selection_keys": removed_keys,
        "replacement_default_key": replacement_default_key,
        "removed_tracks": removed,
        "retained_tracks": retained,
        "expected_audio_signatures": expected_audio,
        "expected_non_audio_signatures": [
            stream for stream in source_snapshot["streams"] if stream["stream_type"] != "audio"
        ],
        "expected_chapters": source_snapshot["chapters"],
        "source_path": source,
        "target_path": target,
        "publish_path": inspection["publish_path"],
        "command": command,
        "command_display": safe_shell_join(command),
        "warnings": inspection["warnings"],
    }


def _active_batch_plan() -> dict[str, Any] | None:
    return db.query_one(
        """
        SELECT * FROM transcode_plans
        WHERE plan_kind = 'audio_cleanup' AND status = 'draft' AND archived_at IS NULL
        ORDER BY id DESC LIMIT 1
        """
    )


def _inflate_batch_item(item: dict[str, Any]) -> dict[str, Any]:
    inflated = dict(item)
    inflated["operation"] = db.loads_json(inflated.get("operation_json"), {})
    inflated["warnings"] = db.loads_json(inflated.get("warnings_json"), [])
    return inflated


def get_batch() -> dict[str, Any]:
    plan = _active_batch_plan()
    if not plan:
        return {"id": None, "name": "Audio cleanup draft", "status": "draft", "items": [], "item_count": 0}
    result = dict(plan)
    result["items"] = [
        _inflate_batch_item(item)
        for item in db.query_all(
            "SELECT * FROM transcode_plan_items WHERE plan_id = ? ORDER BY id", (plan["id"],)
        )
    ]
    result["item_count"] = len(result["items"])
    return result


async def upsert_batch_item(
    file_id: int,
    source_fingerprint: str,
    removed_selection_keys: list[str],
    replacement_default_key: str | None,
) -> dict[str, Any]:
    inspection = await inspect_file(file_id)
    if inspection.get("source_fingerprint") != source_fingerprint:
        raise AudioCleanupError("The source changed after inspection; reopen the audio-track dialog.")
    plan = _active_batch_plan()
    existing = None
    if plan:
        existing = db.query_one(
            "SELECT * FROM transcode_plan_items WHERE plan_id = ? AND file_id = ?",
            (plan["id"], file_id),
        )
    if existing:
        inspection["staged_path"] = existing["target_path"]
    operation = build_operation(
        inspection, removed_selection_keys, replacement_default_key
    )
    now = db.utc_now()
    with db.connect() as connection:
        if not plan:
            cursor = connection.execute(
                """
                INSERT INTO transcode_plans (
                    name, profile_id, status, created_at, updated_at, notes, plan_kind
                ) VALUES ('Audio cleanup draft', NULL, 'draft', ?, ?, ?, 'audio_cleanup')
                """,
                (now, now, "Persistent audio-track cleanup batch."),
            )
            plan_id = cursor.lastrowid
        else:
            plan_id = plan["id"]
        connection.execute(
            "DELETE FROM transcode_plan_items WHERE plan_id = ? AND file_id = ?",
            (plan_id, file_id),
        )
        connection.execute(
            """
            INSERT INTO transcode_plan_items (
                plan_id, file_id, source_path, target_path, action, reason,
                command_json, command_display, warnings_json, operation_type,
                backend, operation_json, publish_path
            ) VALUES (?, ?, ?, ?, 'remove_audio_tracks', ?, ?, ?, ?,
                      'audio_cleanup', ?, ?, ?)
            """,
            (
                plan_id,
                file_id,
                operation["source_path"],
                operation["target_path"],
                f"Remove {len(operation['removed_tracks'])} audio track(s) without re-encoding.",
                db.dumps(operation["command"]),
                operation["command_display"],
                db.dumps(operation["warnings"]),
                operation["backend"],
                db.dumps(operation),
                operation["publish_path"],
            ),
        )
        connection.execute(
            "UPDATE transcode_plans SET updated_at = ? WHERE id = ?", (now, plan_id)
        )
    return get_batch()


def remove_batch_item(file_id: int) -> dict[str, Any]:
    plan = _active_batch_plan()
    if not plan:
        return get_batch()
    db.execute(
        "DELETE FROM transcode_plan_items WHERE plan_id = ? AND file_id = ?", (plan["id"], file_id)
    )
    db.execute("UPDATE transcode_plans SET updated_at = ? WHERE id = ?", (db.utc_now(), plan["id"]))
    return get_batch()


def clear_batch() -> dict[str, Any]:
    plan = _active_batch_plan()
    if plan:
        db.execute("DELETE FROM transcode_plans WHERE id = ?", (plan["id"],))
    return get_batch()


async def prepare_batch_for_queue(name: str | None = None) -> dict[str, Any]:
    batch = get_batch()
    if not batch.get("id") or not batch["items"]:
        raise AudioCleanupError("The audio-cleanup batch is empty.")
    blockers: list[str] = []
    for item in batch["items"]:
        operation = item["operation"]
        inspection = await inspect_file(item["file_id"])
        if inspection.get("source_fingerprint") != operation.get("source_fingerprint"):
            blockers.append(f"{Path(item['source_path']).name}: source changed after it was added.")
            continue
        if inspection.get("blockers"):
            blockers.append(f"{Path(item['source_path']).name}: {'; '.join(inspection['blockers'])}")
        if Path(item["target_path"]).exists():
            blockers.append(f"{Path(item['source_path']).name}: staged target already exists.")
    if blockers:
        raise AudioCleanupError("Batch cannot be queued: " + " ".join(blockers))
    final_name = (name or "").strip() or f"Audio cleanup {db.utc_now()[:16].replace('T', ' ')}"
    db.execute(
        "UPDATE transcode_plans SET name = ?, updated_at = ? WHERE id = ?",
        (final_name[:180], db.utc_now(), batch["id"]),
    )
    return get_batch()


async def preflight_operation(item: dict[str, Any], command: list[str]) -> list[str]:
    operation = db.loads_json(item.get("operation_json"), {})
    if not operation:
        return ["Stored audio-cleanup operation is missing."]
    try:
        inspection = await inspect_file(int(item["file_id"]))
    except AudioCleanupError as exc:
        return [str(exc)]
    errors = list(inspection.get("blockers") or [])
    if inspection.get("source_fingerprint") != operation.get("source_fingerprint"):
        errors.append("Source fingerprint changed after the batch was created.")
    if command != operation.get("command"):
        errors.append("Stored command does not match the server-generated audio-cleanup operation.")
    if item.get("publish_path") != operation.get("publish_path"):
        errors.append("Stored publish path does not match the audio-cleanup operation.")
    return errors


def _counter(signatures: list[dict[str, Any]]) -> Counter[str]:
    return Counter(json.dumps(signature, sort_keys=True, separators=(",", ":")) for signature in signatures)


async def verify_output(item: dict[str, Any]) -> tuple[str, str]:
    operation = db.loads_json(item.get("operation_json"), {})
    target = Path(item["target_path"])
    if not target.exists() or not target.is_file() or target.stat().st_size == 0:
        return "failed", "Output file was not created or is empty."
    try:
        raw = await probe_file(target)
    except ProbeError as exc:
        return "failed", f"Output ffprobe failed: {exc.stderr}"
    if not _is_matroska(raw):
        return "failed", "Output is not a Matroska container."

    source_duration = _float((operation.get("source_snapshot") or {}).get("duration_seconds"))
    target_duration = _duration(raw)
    if source_duration and target_duration:
        tolerance = max(
            CONFIG.transcoder.duration_tolerance_seconds,
            source_duration * CONFIG.transcoder.duration_tolerance_percent,
        )
        if abs(source_duration - target_duration) > tolerance:
            return "failed", f"Duration mismatch: source {source_duration:.2f}s, output {target_duration:.2f}s."

    target_streams = raw.get("streams") or []
    target_audio = [_stream_signature(stream) for stream in target_streams if stream.get("codec_type") == "audio"]
    if _counter(target_audio) != _counter(operation.get("expected_audio_signatures") or []):
        return "failed", "Output audio tracks or their metadata do not match the retained-track selection."
    target_non_audio = [
        _stream_signature(stream) for stream in target_streams if stream.get("codec_type") != "audio"
    ]
    if _counter(target_non_audio) != _counter(operation.get("expected_non_audio_signatures") or []):
        return "failed", "One or more video, subtitle, attachment, or data streams changed or are missing."

    expected_chapters = operation.get("expected_chapters") or []
    target_chapters = [_chapter_signature(chapter) for chapter in raw.get("chapters") or []]
    if len(expected_chapters) != len(target_chapters):
        return "failed", "Chapter count changed during remux."
    for expected, actual in zip(expected_chapters, target_chapters, strict=True):
        if expected.get("title") != actual.get("title"):
            return "failed", "Chapter titles changed during remux."
        for key in ("start", "end"):
            left, right = expected.get(key), actual.get(key)
            if left is None and right is None:
                continue
            if left is None or right is None or abs(float(left) - float(right)) > 0.05:
                return "failed", "Chapter timing changed during remux."
    return (
        "verified",
        f"Verified MKV with {len(target_audio)} retained audio track(s), all non-audio streams, and {len(target_chapters)} chapter(s).",
    )


def refresh_inventory_after_publish(file_id: int, live_path: Path, raw: dict[str, Any]) -> None:
    file_row = db.query_one("SELECT * FROM files WHERE id = ?", (file_id,))
    if not file_row:
        raise AudioCleanupError("Inventory row disappeared before publish completed.")
    normalized = normalize_probe(live_path.resolve(), live_path.stat(), raw)
    recommendation = recommend(normalized)
    normalized.update(
        {
            "recommendation_category": recommendation["category"],
            "recommendation_summary": recommendation["summary"],
            "recommendation_reasons_json": db.dumps(recommendation["reasons"]),
            "recommendation_warnings_json": db.dumps(recommendation["warnings"]),
            "last_seen_at": db.utc_now(),
            "last_scanned_at": db.utc_now(),
            "is_missing": 0,
            "missing_since": None,
            "probe_error": None,
            "probe_exit_code": None,
            "updated_at": db.utc_now(),
        }
    )
    streams = normalized.pop("streams")
    chapters = normalized.pop("chapters")
    old_path = file_row["path"]
    with db.connect() as connection:
        file_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(files)").fetchall()
        }
        excluded = {"id", "root_id", "first_seen_at"}
        values = {key: value for key, value in normalized.items() if key in file_columns - excluded}
        assignments = ", ".join(f"{key} = ?" for key in values)
        connection.execute(
            f"UPDATE files SET {assignments} WHERE id = ?", (*values.values(), file_id)
        )
        connection.execute("DELETE FROM streams WHERE file_id = ?", (file_id,))
        connection.execute("DELETE FROM chapters WHERE file_id = ?", (file_id,))
        if streams:
            stream_columns = [
                row["name"]
                for row in connection.execute("PRAGMA table_info(streams)").fetchall()
                if row["name"] not in {"id"}
            ]
            connection.executemany(
                f"INSERT INTO streams ({', '.join(stream_columns)}) VALUES ({', '.join('?' for _ in stream_columns)})",
                [
                    tuple({**stream, "file_id": file_id}.get(column) for column in stream_columns)
                    for stream in streams
                ],
            )
        if chapters:
            chapter_columns = [
                row["name"]
                for row in connection.execute("PRAGMA table_info(chapters)").fetchall()
                if row["name"] not in {"id"}
            ]
            connection.executemany(
                f"INSERT INTO chapters ({', '.join(chapter_columns)}) VALUES ({', '.join('?' for _ in chapter_columns)})",
                [
                    tuple({**chapter, "file_id": file_id}.get(column) for column in chapter_columns)
                    for chapter in chapters
                ],
            )
        if old_path != str(live_path.resolve()):
            connection.execute("DELETE FROM plex_file_matches WHERE file_id = ?", (file_id,))
