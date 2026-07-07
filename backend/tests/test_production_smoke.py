from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class ProductionSmokeTest(unittest.TestCase):
    def test_temp_database_migrations_and_public_health(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            os.environ.update(
                {
                    "MEDIA_ATLAS_HOST": "127.0.0.1",
                    "MEDIA_ATLAS_DATA_DIR": f"{temp_dir}/data",
                    "MEDIA_ATLAS_REPORTS_DIR": f"{temp_dir}/reports",
                    "MEDIA_ATLAS_LOGS_DIR": f"{temp_dir}/logs",
                    "MEDIA_ATLAS_TRANSCODE_STAGING_DIR": f"{temp_dir}/staging",
                    "MEDIA_ATLAS_AUTH_MODE": "disabled",
                }
            )
            from fastapi.testclient import TestClient

            from app import db
            from app.config import CONFIG
            from app.main import app, transcode_manager
            from app.services.transcodes import build_command

            db.init_db()
            self.assertTrue(db.migration_status()["ok"])
            self.assertIn("0001_initial_schema", db.migration_status()["applied"])
            self.assertIn("0002_archive_transcode_plans", db.migration_status()["applied"])
            self.assertIn("0003_publish_transcode_items", db.migration_status()["applied"])
            self.assertIn("0004_publish_progress", db.migration_status()["applied"])
            self.assertIn("0005_transcode_run_cleanup_archive", db.migration_status()["applied"])
            self.assertEqual(CONFIG.transcoder.backup_dir, (Path(temp_dir) / "transcode-backups").resolve())
            profiles = db.query_all("SELECT name, command_template FROM transcode_profiles")
            templates = {profile["command_template"] for profile in profiles}
            self.assertTrue(
                {
                    "hevc_archive",
                    "hevc_archive_fast",
                    "hevc_archive_faster",
                    "hevc_qsv",
                    "hevc_nvenc",
                    "hevc_vaapi",
                }.issubset(templates)
            )
            self.assertEqual(
                build_command({"command_template": "hevc_archive_fast"}, "/source.mkv", "/target.mkv"),
                [
                    "ffmpeg",
                    "-n",
                    "-i",
                    "/source.mkv",
                    "-map",
                    "0",
                    "-c:v",
                    "libx265",
                    "-crf",
                    "21",
                    "-preset",
                    "fast",
                    "-c:a",
                    "copy",
                    "-c:s",
                    "copy",
                    "/target.mkv",
                ],
            )
            self.assertIn(
                "hevc_nvenc",
                build_command({"command_template": "hevc_nvenc"}, "/source.mkv", "/target.mkv") or [],
            )
            self.assertIn(
                "hevc_vaapi",
                build_command({"command_template": "hevc_vaapi"}, "/source.mkv", "/target.mkv") or [],
            )
            media_dir = Path(temp_dir) / "media"
            media_dir.mkdir()
            source_path = media_dir / "Movie.mkv"
            target_path = CONFIG.transcoder.staging_dir / "Movie.transcoded.mkv"
            source_path.write_bytes(b"original media bytes")
            target_path.write_bytes(b"transcoded media bytes")
            now = db.utc_now()
            run_id = db.execute(
                """
                INSERT INTO transcode_runs (name, status, created_at, total_items, completed_items)
                VALUES (?, 'succeeded', ?, 1, 1)
                """,
                ("Publish test", now),
            )
            item_id = db.execute(
                """
                INSERT INTO transcode_run_items (
                    run_id, status, source_path, target_path, command_json, command_display,
                    verification_status, verification_message, warnings_json, created_at, finished_at
                )
                VALUES (?, 'succeeded', ?, ?, ?, ?, 'verified', 'ok', '[]', ?, ?)
                """,
                (
                    run_id,
                    str(source_path),
                    str(target_path),
                    '["ffmpeg"]',
                    "ffmpeg",
                    now,
                    now,
                ),
            )
            published = transcode_manager.publish_item(
                run_id,
                item_id,
                str(source_path),
                str(target_path),
                "REPLACE",
            )
            backup_path = Path(published["published_backup_path"])
            self.assertEqual(source_path.read_bytes(), b"transcoded media bytes")
            self.assertEqual(backup_path.read_bytes(), b"original media bytes")
            self.assertTrue(backup_path.is_relative_to(CONFIG.transcoder.backup_dir))
            self.assertEqual(published["publish_status"], "published")
            self.assertEqual(published["publish_step"], "completed")
            self.assertEqual(published["publish_progress_percent"], 100)
            self.assertEqual(
                published["publish_bytes_total"],
                len(b"original media bytes") + len(b"transcoded media bytes"),
            )
            self.assertEqual(list(media_dir.glob("*media-atlas-backup*")), [])
            cleaned = transcode_manager.cleanup_run_artifacts(run_id, "DELETE ARTIFACTS")
            self.assertEqual(source_path.read_bytes(), b"transcoded media bytes")
            self.assertFalse(target_path.exists())
            self.assertFalse(backup_path.exists())
            self.assertTrue(cleaned["archived_at"])
            cleaned_item = cleaned["items"][0]
            self.assertEqual(cleaned_item["cleanup_status"], "cleaned")
            self.assertTrue(cleaned_item["staged_deleted_at"])
            self.assertTrue(cleaned_item["backup_deleted_at"])

            with TestClient(app) as client:
                live = client.get("/api/health/live")
                self.assertEqual(live.status_code, 200)
                self.assertEqual(live.json()["status"], "alive")

                auth = client.get("/api/auth/me")
                self.assertEqual(auth.status_code, 200)
                self.assertTrue(auth.json()["authenticated"])


if __name__ == "__main__":
    unittest.main()
