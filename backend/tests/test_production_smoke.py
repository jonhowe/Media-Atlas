from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

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
            from app.config import CONFIG, load_config
            from app.main import app, scan_manager, transcode_manager
            from app.services import scanner as scanner_module
            from app.services.transcodes import build_command, transcode_savings_stats

            db.init_db()
            self.assertTrue(db.migration_status()["ok"])
            self.assertIn("0001_initial_schema", db.migration_status()["applied"])
            self.assertIn("0002_archive_transcode_plans", db.migration_status()["applied"])
            self.assertIn("0003_publish_transcode_items", db.migration_status()["applied"])
            self.assertIn("0004_publish_progress", db.migration_status()["applied"])
            self.assertIn("0005_transcode_run_cleanup_archive", db.migration_status()["applied"])
            self.assertIn("0006_transcode_savings", db.migration_status()["applied"])
            self.assertEqual(CONFIG.transcoder.backup_dir, (Path(temp_dir) / "transcode-backups").resolve())
            with tempfile.TemporaryDirectory() as config_temp_dir:
                config_env = {
                    "MEDIA_ATLAS_HOST": "0.0.0.0",
                    "MEDIA_ATLAS_DATA_DIR": f"{config_temp_dir}/data",
                    "MEDIA_ATLAS_REPORTS_DIR": f"{config_temp_dir}/reports",
                    "MEDIA_ATLAS_LOGS_DIR": f"{config_temp_dir}/logs",
                    "MEDIA_ATLAS_TRANSCODE_STAGING_DIR": f"{config_temp_dir}/staging",
                    "MEDIA_ATLAS_AUTH_MODE": "disabled",
                    "MEDIA_ATLAS_ACKNOWLEDGE_AUTH_DISABLED_LAN": "true",
                }
                with patch.dict(os.environ, config_env, clear=False):
                    acknowledged_config = load_config()
                self.assertTrue(acknowledged_config.operations.acknowledge_auth_disabled_lan)
                self.assertEqual(acknowledged_config.host, "0.0.0.0")
                self.assertEqual(acknowledged_config.auth.mode, "disabled")
                self.assertEqual(acknowledged_config.config_warnings, [])
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

            async def scan_responsiveness_check() -> None:
                scan_root = Path(temp_dir) / "scan-root"
                scan_root.mkdir()
                include_extensions, exclude_patterns = db.default_root_payload()
                now = db.utc_now()
                db.execute(
                    """
                    INSERT INTO media_roots (
                        name, path, enabled, include_extensions_json, exclude_patterns_json, created_at, updated_at
                    )
                    VALUES (?, ?, 1, ?, ?, ?, ?)
                    """,
                    ("Slow scan root", str(scan_root), include_extensions, exclude_patterns, now, now),
                )
                ticks = 0

                def slow_discovery(root: dict) -> list[Path]:
                    time.sleep(0.25)
                    return []

                async def ticker() -> None:
                    nonlocal ticks
                    deadline = time.monotonic() + 0.15
                    while time.monotonic() < deadline:
                        ticks += 1
                        await asyncio.sleep(0.01)

                with patch.object(scanner_module, "discover_media_files", side_effect=slow_discovery):
                    scan = await scan_manager.start_scan()
                    await ticker()
                    self.assertGreater(ticks, 3)
                    scan_manager.cancel_scan(scan["id"])
                    await asyncio.sleep(0.2)
                    finished_scan = db.query_one("SELECT * FROM scan_jobs WHERE id = ?", (scan["id"],))
                    self.assertIn(finished_scan["status"], {"canceled", "succeeded"})

            asyncio.run(scan_responsiveness_check())

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
            self.assertEqual(published["source_size_bytes"], len(b"original media bytes"))
            self.assertEqual(published["output_size_bytes"], len(b"transcoded media bytes"))
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
            savings = transcode_savings_stats()
            self.assertEqual(savings["runs_started"], 0)
            self.assertEqual(savings["items_succeeded"], 1)
            self.assertEqual(savings["total_source_size_bytes"], len(b"original media bytes"))
            self.assertEqual(savings["total_output_size_bytes"], len(b"transcoded media bytes"))
            self.assertEqual(
                savings["total_space_saved_bytes"],
                len(b"original media bytes") - len(b"transcoded media bytes"),
            )

            partial_target = CONFIG.transcoder.staging_dir / "Partial.transcoded.mkv"
            partial_target.write_bytes(b"partial transcoded media bytes")
            other_target = CONFIG.transcoder.staging_dir / "Other.transcoded.mkv"
            other_target.write_bytes(b"other transcoded media bytes")
            partial_backup = CONFIG.transcoder.backup_dir / "run-partial" / "item-1" / "Partial.backup.mkv"
            partial_backup.parent.mkdir(parents=True)
            partial_backup.write_bytes(b"partial original media bytes")
            partial_run_id = db.execute(
                """
                INSERT INTO transcode_runs (name, status, created_at, total_items, completed_items)
                VALUES (?, 'succeeded', ?, 2, 2)
                """,
                ("Partial cleanup test", now),
            )
            partial_item_id = db.execute(
                """
                INSERT INTO transcode_run_items (
                    run_id, status, source_path, target_path, command_json, command_display,
                    verification_status, verification_message, warnings_json, created_at, finished_at,
                    published_at, published_backup_path
                )
                VALUES (?, 'succeeded', ?, ?, ?, ?, 'verified', 'ok', '[]', ?, ?, ?, ?)
                """,
                (
                    partial_run_id,
                    str(media_dir / "Partial.mkv"),
                    str(partial_target),
                    '["ffmpeg"]',
                    "ffmpeg",
                    now,
                    now,
                    now,
                    str(partial_backup),
                ),
            )
            unpublished_item_id = db.execute(
                """
                INSERT INTO transcode_run_items (
                    run_id, status, source_path, target_path, command_json, command_display,
                    verification_status, verification_message, warnings_json, created_at, finished_at
                )
                VALUES (?, 'succeeded', ?, ?, ?, ?, 'verified', 'ok', '[]', ?, ?)
                """,
                (
                    partial_run_id,
                    str(media_dir / "Other.mkv"),
                    str(other_target),
                    '["ffmpeg"]',
                    "ffmpeg",
                    now,
                    now,
                ),
            )
            item_cleanup = transcode_manager.cleanup_item_artifacts(
                partial_run_id,
                partial_item_id,
                "DELETE ARTIFACTS",
            )
            self.assertEqual(item_cleanup["cleanup_status"], "cleaned")
            self.assertFalse(partial_target.exists())
            self.assertFalse(partial_backup.exists())
            self.assertTrue(other_target.exists())
            with self.assertRaises(ValueError):
                transcode_manager.cleanup_item_artifacts(partial_run_id, unpublished_item_id, "DELETE ARTIFACTS")
            partial_bulk_cleanup = transcode_manager.cleanup_run_artifacts(partial_run_id, "DELETE ARTIFACTS")
            self.assertIsNone(partial_bulk_cleanup["archived_at"])
            self.assertFalse(partial_bulk_cleanup["cleanup_summary"]["run_archived"])

            with TestClient(app) as client:
                live = client.get("/api/health/live")
                self.assertEqual(live.status_code, 200)
                self.assertEqual(live.json()["status"], "alive")

                auth = client.get("/api/auth/me")
                self.assertEqual(auth.status_code, 200)
                self.assertTrue(auth.json()["authenticated"])

                admin_status = client.get("/api/admin/status")
                self.assertEqual(admin_status.status_code, 200)
                runtime_config = admin_status.json()["runtime_config"]
                self.assertEqual(runtime_config["host"], "127.0.0.1")
                self.assertEqual(runtime_config["port"], 8000)
                self.assertEqual(runtime_config["auth"], {"mode": "disabled"})
                self.assertEqual(
                    set(runtime_config["operations"]),
                    {"acknowledge_auth_disabled_lan", "fail_unsafe_bind", "allowed_origins"},
                )

                stats = client.get("/api/transcode-runs/stats")
                self.assertEqual(stats.status_code, 200)
                self.assertEqual(stats.json()["items_with_size_comparison"], 1)

                item_cleanup_response = client.post(
                    f"/api/transcode-runs/{partial_run_id}/items/{partial_item_id}/cleanup",
                    json={"confirmation_text": "DELETE ARTIFACTS"},
                )
                self.assertEqual(item_cleanup_response.status_code, 200)
                self.assertEqual(item_cleanup_response.json()["cleanup_status"], "cleaned")


if __name__ == "__main__":
    unittest.main()
