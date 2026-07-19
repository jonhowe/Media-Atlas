from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
import unittest
from dataclasses import replace
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
                    "MEDIA_ATLAS_VERSION": "v9.8.7-test",
                    "MEDIA_ATLAS_GIT_SHA": "0123456789abcdef",
                    "MEDIA_ATLAS_BUILD_DATE": "2026-07-18T12:00:00Z",
                    "MEDIA_ATLAS_IMAGE_TAG": "v9.8.7-test",
                }
            )
            from fastapi.testclient import TestClient

            from app import db
            from app.config import CONFIG, load_config

            fresh_config = load_config()
            for field_name in fresh_config.__dataclass_fields__:
                object.__setattr__(CONFIG, field_name, getattr(fresh_config, field_name))

            from app.logging_config import application_log_path
            from app.main import app, scan_manager, transcode_manager
            from app.services import scanner as scanner_module
            from app.services.transcodes import build_command, create_plan, transcode_savings_stats

            db.init_db()
            self.assertTrue(db.migration_status()["ok"])
            self.assertIn("0001_initial_schema", db.migration_status()["applied"])
            self.assertIn("0002_archive_transcode_plans", db.migration_status()["applied"])
            self.assertIn("0003_publish_transcode_items", db.migration_status()["applied"])
            self.assertIn("0004_publish_progress", db.migration_status()["applied"])
            self.assertIn("0005_transcode_run_cleanup_archive", db.migration_status()["applied"])
            self.assertIn("0006_transcode_savings", db.migration_status()["applied"])
            self.assertIn("0007_publish_validation_and_indexes", db.migration_status()["applied"])
            self.assertIn("0008_media_retention_review", db.migration_status()["applied"])
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
            self.assertEqual(
                build_command({"command_template": "remux_mkv"}, "/source.ts", "/target.mkv"),
                [
                    "ffmpeg",
                    "-n",
                    "-i",
                    "/source.ts",
                    "-map",
                    "0",
                    "-c",
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
            self.assertIsNone(published["validated_at"])
            self.assertEqual(published["source_size_bytes"], len(b"original media bytes"))
            self.assertEqual(published["output_size_bytes"], len(b"transcoded media bytes"))
            self.assertEqual(
                published["publish_bytes_total"],
                len(b"original media bytes") + len(b"transcoded media bytes"),
            )
            self.assertEqual(list(media_dir.glob("*media-atlas-backup*")), [])
            with self.assertRaises(ValueError):
                transcode_manager.cleanup_item_artifacts(run_id, item_id, "DELETE ARTIFACTS")
            validated = transcode_manager.validate_item(run_id, item_id, "VALIDATED")
            self.assertTrue(validated["validated_at"])
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
                    published_at, published_backup_path, validated_at
                )
                VALUES (?, 'succeeded', ?, ?, ?, ?, 'verified', 'ok', '[]', ?, ?, ?, ?, ?)
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
                    now,
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

            root_id = db.query_one("SELECT id FROM media_roots ORDER BY id LIMIT 1")["id"]
            planning_file_id = db.execute(
                """
                INSERT INTO files (
                    root_id, path, directory, filename, extension, size_bytes,
                    modified_time_ns, first_seen_at, last_seen_at, updated_at,
                    recommendation_category, recommendation_summary,
                    recommendation_reasons_json, recommendation_warnings_json
                )
                VALUES (?, ?, ?, ?, '.mkv', 100, 1, ?, ?, ?, 'Review', ?, '[]', '[]')
                """,
                (
                    root_id,
                    str(source_path),
                    str(source_path.parent),
                    source_path.name,
                    now,
                    now,
                    now,
                    "Complex media should be reviewed before conversion.",
                ),
            )
            manual_profile_id = db.query_one(
                "SELECT id FROM transcode_profiles WHERE command_template = 'manual_review'"
            )["id"]
            review_plan = create_plan("Review-only plan", manual_profile_id, [planning_file_id])

            active_app_log = application_log_path(CONFIG.logs_dir)
            active_app_log.parent.mkdir(parents=True, exist_ok=True)
            active_app_log.write_text(
                '{"timestamp":"2026-07-18T10:00:00+00:00","level":"info","logger":"media_atlas.smoke","message":"retained active log"}\n',
                encoding="utf-8",
            )
            expired_rotated_log = active_app_log.with_name(f"{active_app_log.name}.2020-01-01")
            expired_rotated_log.write_text("expired\n", encoding="utf-8")
            expired_time = time.time() - (40 * 24 * 60 * 60)
            os.utime(active_app_log, (expired_time, expired_time))
            os.utime(expired_rotated_log, (expired_time, expired_time))

            with TestClient(app) as client:
                self.assertTrue(active_app_log.exists())
                self.assertFalse(expired_rotated_log.exists())
                live = client.get("/api/health/live")
                self.assertEqual(live.status_code, 200)
                self.assertEqual(live.json()["status"], "alive")

                auth = client.get("/api/auth/me")
                self.assertEqual(auth.status_code, 200)
                self.assertTrue(auth.json()["authenticated"])

                version = client.get("/api/version")
                self.assertEqual(version.status_code, 200)
                self.assertEqual(
                    version.json(),
                    {
                        "version": "v9.8.7-test",
                        "git_sha": "0123456789abcdef",
                        "build_date": "2026-07-18T12:00:00Z",
                        "image_tag": "v9.8.7-test",
                    },
                )

                original_auth = CONFIG.auth
                object.__setattr__(
                    CONFIG,
                    "auth",
                    replace(
                        original_auth,
                        mode="single_admin",
                        admin_password="docs-test-password",
                        session_secret="docs-test-session-secret",
                    ),
                )
                try:
                    unauthenticated_version = client.get("/api/version")
                    self.assertEqual(unauthenticated_version.status_code, 401)
                    login_response = client.post(
                        "/api/auth/login",
                        json={"username": original_auth.admin_username, "password": "docs-test-password"},
                    )
                    self.assertEqual(login_response.status_code, 200)
                    authenticated_version = client.get("/api/version")
                    self.assertEqual(authenticated_version.status_code, 200)
                finally:
                    object.__setattr__(CONFIG, "auth", original_auth)

                application_logs = client.get("/api/logs/application", params={"logger": "media_atlas", "limit": 50})
                self.assertEqual(application_logs.status_code, 200)
                self.assertTrue(application_logs.json()["items"])
                self.assertTrue(all("message" in item for item in application_logs.json()["items"]))
                successful_poll_logs = client.get(
                    "/api/logs/application",
                    params={"logger": "media_atlas.requests", "query": "/api/logs/application"},
                )
                self.assertEqual(successful_poll_logs.json()["items"], [])
                failed_poll = client.get("/api/logs/application", params={"limit": 501})
                self.assertEqual(failed_poll.status_code, 422)
                failed_poll_logs = client.get(
                    "/api/logs/application",
                    params={"logger": "media_atlas.requests", "query": "/api/logs/application"},
                )
                self.assertTrue(
                    any(item.get("status_code") == 422 for item in failed_poll_logs.json()["items"]),
                    failed_poll_logs.json(),
                )

                plans = client.get("/api/transcode-plans")
                self.assertEqual(plans.status_code, 200)
                review_plan_summary = next(item for item in plans.json() if item["id"] == review_plan["id"])
                self.assertEqual(review_plan_summary["item_count"], 1)
                self.assertEqual(review_plan_summary["runnable_item_count"], 0)

                retention_connection = client.post(
                    "/api/retention/connections",
                    json={
                        "service_type": "radarr",
                        "name": "Smoke Radarr",
                        "server_url": "http://radarr.invalid",
                        "api_key": "retention-smoke-secret",
                        "seerr_service_id": 10,
                        "path_mappings": [
                            {"source_path_prefix": "/movies", "media_atlas_path_prefix": "/media/Movies"}
                        ],
                    },
                )
                self.assertEqual(retention_connection.status_code, 200)
                self.assertNotIn("retention-smoke-secret", retention_connection.text)
                retention_connection_id = retention_connection.json()["id"]
                retention_connections = client.get("/api/retention/connections")
                self.assertEqual(retention_connections.status_code, 200)
                self.assertNotIn("retention-smoke-secret", retention_connections.text)
                retention_export = client.get("/api/exports/retention-candidates.csv")
                self.assertEqual(retention_export.status_code, 200)
                retention_results_export = client.get("/api/exports/retention-results.csv")
                self.assertEqual(retention_results_export.status_code, 200)

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
                self.assertEqual(admin_status.json()["version"]["version"], "v9.8.7-test")

                diagnostics = client.get("/api/admin/diagnostics")
                self.assertEqual(diagnostics.status_code, 200)
                diagnostics_body = diagnostics.json()
                self.assertIn("runtime_config", diagnostics_body)
                self.assertIn("version", diagnostics_body)
                self.assertNotIn("MEDIA_ATLAS_PLEX_TOKEN", diagnostics.text)
                self.assertNotIn("retention-smoke-secret", diagnostics.text)
                self.assertIn("media_retention", diagnostics_body)
                self.assertIn("retention_analyses", diagnostics_body["readiness"]["jobs"])

                stats = client.get("/api/transcode-runs/stats")
                self.assertEqual(stats.status_code, 200)
                self.assertEqual(stats.json()["items_with_size_comparison"], 1)
                self.assertEqual(stats.json()["items_validated"], 2)

                item_cleanup_response = client.post(
                    f"/api/transcode-runs/{partial_run_id}/items/{partial_item_id}/cleanup",
                    json={"confirmation_text": "DELETE ARTIFACTS"},
                )
                self.assertEqual(item_cleanup_response.status_code, 200)
                self.assertEqual(item_cleanup_response.json()["cleanup_status"], "cleaned")

                removed_connection = client.delete(f"/api/retention/connections/{retention_connection_id}")
                self.assertEqual(removed_connection.status_code, 200)


if __name__ == "__main__":
    unittest.main()
