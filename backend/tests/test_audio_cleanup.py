from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def probe_payload(container: str = "matroska,webm") -> dict:
    return {
        "format": {"format_name": container, "duration": "60.000", "bit_rate": "1000000"},
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "profile": "High",
                "width": 1920,
                "height": 1080,
                "pix_fmt": "yuv420p",
                "disposition": {"default": 1, "forced": 0},
                "tags": {},
            },
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "aac",
                "profile": "LC",
                "channels": 2,
                "channel_layout": "stereo",
                "sample_rate": "48000",
                "bit_rate": "192000",
                "disposition": {"default": 1, "forced": 0},
                "tags": {"language": "eng", "title": "Main"},
            },
            {
                "index": 2,
                "codec_type": "audio",
                "codec_name": "aac",
                "profile": "LC",
                "channels": 2,
                "channel_layout": "stereo",
                "sample_rate": "48000",
                "bit_rate": "128000",
                "disposition": {"default": 0, "forced": 0},
                "tags": {"language": "eng", "title": "Commentary"},
            },
        ],
        "chapters": [
            {"start_time": "0.000", "end_time": "60.000", "tags": {"title": "Feature"}}
        ],
    }


class AudioCleanupTest(unittest.TestCase):
    def setUp(self) -> None:
        from app import db
        from app.config import CONFIG

        self.db = db
        self.config = CONFIG
        self.original_database_path = CONFIG.database_path
        self.original_transcoder = CONFIG.transcoder
        self.temp_dir = tempfile.TemporaryDirectory()
        base = Path(self.temp_dir.name)
        object.__setattr__(CONFIG, "database_path", base / "audio-cleanup.sqlite")
        object.__setattr__(
            CONFIG,
            "transcoder",
            replace(
                CONFIG.transcoder,
                staging_dir=base / "staging",
                backup_dir=base / "backups",
                mkvmerge_path="mkvmerge",
                min_free_bytes=0,
            ),
        )
        CONFIG.transcoder.staging_dir.mkdir()
        CONFIG.transcoder.backup_dir.mkdir()
        db.init_db()
        now = db.utc_now()
        self.media_dir = base / "media"
        self.media_dir.mkdir()
        self.root_id = db.execute(
            """
            INSERT INTO media_roots (
                name, path, enabled, include_extensions_json, exclude_patterns_json,
                created_at, updated_at
            ) VALUES ('Movies', ?, 1, '[".mkv",".mp4"]', '[]', ?, ?)
            """,
            (str(self.media_dir), now, now),
        )

    def tearDown(self) -> None:
        object.__setattr__(self.config, "database_path", self.original_database_path)
        object.__setattr__(self.config, "transcoder", self.original_transcoder)
        self.temp_dir.cleanup()

    def create_file(self, suffix: str) -> tuple[int, Path]:
        path = self.media_dir / f"Feature{suffix}"
        path.write_bytes(b"source media")
        now = self.db.utc_now()
        file_id = self.db.execute(
            """
            INSERT INTO files (
                root_id, path, directory, filename, extension, size_bytes,
                modified_time_ns, first_seen_at, last_seen_at, updated_at,
                audio_stream_count, recommendation_reasons_json,
                recommendation_warnings_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 2, '[]', '[]')
            """,
            (
                self.root_id,
                str(path),
                str(path.parent),
                path.name,
                suffix,
                path.stat().st_size,
                path.stat().st_mtime_ns,
                now,
                now,
                now,
            ),
        )
        return file_id, path

    def test_mkv_inspection_and_command_use_identified_track_ids(self) -> None:
        from app.services import audio_cleanup

        file_id, _ = self.create_file(".mkv")
        identified = {"tracks": [{"id": 0, "type": "video"}, {"id": 7, "type": "audio"}, {"id": 9, "type": "audio"}]}
        with (
            patch.object(audio_cleanup, "probe_file", AsyncMock(return_value=probe_payload())),
            patch.object(audio_cleanup, "_identify_mkvmerge", AsyncMock(return_value=identified)),
        ):
            inspection = asyncio.run(audio_cleanup.inspect_file(file_id))
        self.assertEqual(inspection["backend"], "mkvmerge")
        self.assertEqual([track["selection_key"] for track in inspection["audio_tracks"]], ["mkvmerge:7", "mkvmerge:9"])
        operation = audio_cleanup.build_operation(
            inspection, ["mkvmerge:7"], "mkvmerge:9"
        )
        self.assertIn("!7", operation["command"])
        self.assertIn("9:1", operation["command"])
        self.assertEqual(operation["command"][-1], inspection["source_path"])
        with self.assertRaises(audio_cleanup.AudioCleanupError):
            audio_cleanup.build_operation(
                inspection, ["mkvmerge:7", "mkvmerge:9"], None
            )

    def test_ffmpeg_batch_persists_and_output_verification_is_exact(self) -> None:
        from app.services import audio_cleanup, transcodes

        file_id, _ = self.create_file(".mp4")
        source_probe = probe_payload("mov,mp4,m4a,3gp,3g2,mj2")
        with (
            patch.object(audio_cleanup, "probe_file", AsyncMock(return_value=source_probe)),
            patch.object(audio_cleanup, "_tool_available", return_value=True),
        ):
            inspection = asyncio.run(audio_cleanup.inspect_file(file_id))
            batch = asyncio.run(
                audio_cleanup.upsert_batch_item(
                    file_id,
                    inspection["source_fingerprint"],
                    ["ffmpeg:1"],
                    "ffmpeg:2",
                )
            )
        self.assertEqual(batch["item_count"], 1)
        item = batch["items"][0]
        command = item["operation"]["command"]
        self.assertIn("-0:1", command)
        self.assertEqual(command[-1], item["target_path"])
        self.assertTrue(item["publish_path"].endswith("Feature.mkv"))
        self.assertEqual(audio_cleanup.get_batch()["item_count"], 1)

        manager = transcodes.TranscodeManager()
        with self.assertRaisesRegex(ValueError, "audio-cleanup queue endpoint"):
            asyncio.run(manager.create_run(batch["id"]))
        with patch.object(manager, "ensure_worker", AsyncMock()):
            run = asyncio.run(
                manager.create_run(batch["id"], audio_cleanup_validated=True)
            )
        self.assertEqual(run["run_kind"], "audio_cleanup")
        self.assertEqual(
            self.db.query_one("SELECT status FROM transcode_plans WHERE id = ?", (batch["id"],))["status"],
            "submitted",
        )
        with self.assertRaisesRegex(ValueError, "already been submitted"):
            asyncio.run(
                manager.create_run(batch["id"], audio_cleanup_validated=True)
            )

        output_probe = probe_payload()
        output_probe["streams"] = [output_probe["streams"][0], output_probe["streams"][2]]
        output_probe["streams"][1]["disposition"]["default"] = 1
        target = Path(item["target_path"])
        target.parent.mkdir(parents=True)
        target.write_bytes(b"staged mkv")
        run_item = {**item, "operation_json": json.dumps(item["operation"])}
        with patch.object(audio_cleanup, "probe_file", AsyncMock(return_value=output_probe)):
            status, message = asyncio.run(audio_cleanup.verify_output(run_item))
        self.assertEqual(status, "verified", message)

        broken_probe = probe_payload()
        broken_probe["streams"] = [broken_probe["streams"][2]]
        broken_probe["streams"][0]["disposition"]["default"] = 1
        with patch.object(audio_cleanup, "probe_file", AsyncMock(return_value=broken_probe)):
            status, _ = asyncio.run(audio_cleanup.verify_output(run_item))
        self.assertEqual(status, "failed")

    def test_publish_renames_non_mkv_and_refreshes_inventory(self) -> None:
        from app.services import transcodes

        file_id, source = self.create_file(".mp4")
        target = self.config.transcoder.staging_dir / "Feature.audio-cleanup.mkv"
        target.write_bytes(b"staged mkv bytes")
        destination = source.with_suffix(".mkv")
        now = self.db.utc_now()
        run_id = self.db.execute(
            """
            INSERT INTO transcode_runs (
                name, status, created_at, total_items, completed_items, run_kind
            ) VALUES ('Audio cleanup', 'succeeded', ?, 1, 1, 'audio_cleanup')
            """,
            (now,),
        )
        item_id = self.db.execute(
            """
            INSERT INTO transcode_run_items (
                run_id, file_id, status, source_path, target_path, command_json,
                command_display, verification_status, verification_message,
                warnings_json, created_at, finished_at, operation_type, backend,
                operation_json, publish_path
            ) VALUES (?, ?, 'succeeded', ?, ?, '["ffmpeg"]', 'ffmpeg',
                      'verified', 'ok', '[]', ?, ?, 'audio_cleanup', 'ffmpeg', '{}', ?)
            """,
            (run_id, file_id, str(source), str(target), now, now, str(destination)),
        )
        manager = transcodes.TranscodeManager()
        with (
            patch.object(transcodes, "preflight_audio_cleanup", AsyncMock(return_value=[])),
            patch.object(transcodes, "probe_file", AsyncMock(return_value=probe_payload())),
        ):
            result = manager.publish_item(
                run_id,
                item_id,
                str(source),
                str(target),
                "REPLACE",
                str(destination),
            )
        self.assertFalse(source.exists())
        self.assertEqual(destination.read_bytes(), b"staged mkv bytes")
        self.assertTrue(Path(result["published_backup_path"]).exists())
        refreshed = self.db.query_one("SELECT path, extension, audio_stream_count FROM files WHERE id = ?", (file_id,))
        self.assertEqual(refreshed["path"], str(destination.resolve()))
        self.assertEqual(refreshed["extension"], ".mkv")
        self.assertEqual(refreshed["audio_stream_count"], 2)


if __name__ == "__main__":
    unittest.main()
