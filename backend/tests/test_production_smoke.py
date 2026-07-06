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
            from app.main import app
            from app.services.transcodes import build_command

            db.init_db()
            self.assertTrue(db.migration_status()["ok"])
            self.assertIn("0001_initial_schema", db.migration_status()["applied"])
            self.assertIn("0002_archive_transcode_plans", db.migration_status()["applied"])
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

            with TestClient(app) as client:
                live = client.get("/api/health/live")
                self.assertEqual(live.status_code, 200)
                self.assertEqual(live.json()["status"], "alive")

                auth = client.get("/api/auth/me")
                self.assertEqual(auth.status_code, 200)
                self.assertTrue(auth.json()["authenticated"])


if __name__ == "__main__":
    unittest.main()
