from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = REPO_ROOT / "scripts" / "generate_docs_demo.py"


class DocumentationDemoTest(unittest.TestCase):
    def test_generator_creates_migrated_synthetic_data_and_read_apis(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "docs-demo"
            result = subprocess.run(
                [
                    sys.executable,
                    str(GENERATOR),
                    "--output-dir",
                    str(output_dir),
                    "--version",
                    "v9.9.9-docs",
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            summary = json.loads(result.stdout)
            self.assertEqual(summary["files"], 12)
            self.assertEqual(summary["plans"], 2)
            self.assertEqual(summary["retention_candidates"], 3)

            database = output_dir / "data" / "media_inventory.sqlite"
            with sqlite3.connect(database) as connection:
                migrations = connection.execute("SELECT version FROM schema_migrations").fetchall()
                self.assertGreaterEqual(len(migrations), 9)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM files").fetchone()[0], 12)
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM plex_file_matches").fetchone()[0],
                    8,
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM retention_candidates").fetchone()[0],
                    3,
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM retention_review_items").fetchone()[0],
                    3,
                )
                secrets = connection.execute(
                    "SELECT GROUP_CONCAT(server_url || ' ' || api_key, ' ') FROM retention_connections"
                ).fetchone()[0]
                self.assertNotIn("192.168.", secrets)
                self.assertIn(".demo.invalid", secrets)
                user_visible_paths = connection.execute(
                    """
                    SELECT source_path, target_path, command_display, NULL AS published_backup_path
                    FROM transcode_plan_items
                    UNION ALL
                    SELECT source_path, target_path, command_display, published_backup_path
                    FROM transcode_run_items
                    """
                ).fetchall()
                for source_path, target_path, command_display, backup_path in user_visible_paths:
                    self.assertTrue(source_path.startswith("/demo/media/"))
                    self.assertTrue(target_path.startswith("/demo/media/"))
                    self.assertNotIn(temp_dir, command_display)
                    if backup_path:
                        self.assertTrue(backup_path.startswith("/demo/media/"))

            port = _free_port()
            environment = os.environ.copy()
            environment.update(
                {
                    "PYTHONPATH": str(REPO_ROOT / "backend"),
                    "MEDIA_ATLAS_HOST": "127.0.0.1",
                    "MEDIA_ATLAS_PORT": str(port),
                    "MEDIA_ATLAS_DATA_DIR": str(output_dir / "data"),
                    "MEDIA_ATLAS_REPORTS_DIR": str(output_dir / "reports"),
                    "MEDIA_ATLAS_LOGS_DIR": str(output_dir / "logs"),
                    "MEDIA_ATLAS_TRANSCODE_STAGING_DIR": str(output_dir / "transcode-staging"),
                    "MEDIA_ATLAS_TRANSCODE_BACKUP_DIR": str(output_dir / "transcode-backups"),
                    "MEDIA_ATLAS_ALLOWED_BROWSE_ROOTS": "/demo/media",
                    "MEDIA_ATLAS_AUTH_MODE": "disabled",
                    "MEDIA_ATLAS_VERSION": "v9.9.9-docs",
                    "MEDIA_ATLAS_GIT_SHA": "docs-test-sha",
                    "MEDIA_ATLAS_BUILD_DATE": "2026-07-01T12:00:00Z",
                    "MEDIA_ATLAS_IMAGE_TAG": "v9.9.9-docs",
                    "MEDIA_ATLAS_READINESS_MIN_FREE_BYTES": "0",
                }
            )
            server = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                cwd=REPO_ROOT / "backend",
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            try:
                _wait_for_server(port, server)
                self.assertEqual(_get_json(port, "/api/version")["version"], "v9.9.9-docs")
                self.assertEqual(_get_json(port, "/api/reports/summary")["total_files"], 12)
                self.assertEqual(_get_json(port, "/api/media?page=1&page_size=50")["total"], 12)
                retention_summary = _get_json(port, "/api/retention/summary")
                self.assertEqual(retention_summary["candidate_count"], 2)
                self.assertEqual(retention_summary["review_ready_scope_count"], 2)
                self.assertEqual(_get_json(port, "/api/retention/results?decision=all")["total"], 3)
                self.assertEqual(len(_get_json(port, "/api/transcode-runs?include_archived=true")), 2)
                self.assertEqual(len(_get_json(port, "/api/scans")), 2)
                self.assertGreaterEqual(len(_get_json(port, "/api/logs/application")["items"]), 6)
                admin_status = _get_json(port, "/api/admin/status")
                self.assertEqual(admin_status["version"]["version"], "v9.9.9-docs")
                self.assertEqual(admin_status["readiness"]["migrations"]["pending"], [])
            finally:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)
                if server.stdout:
                    server.stdout.close()

    def test_generator_refuses_nonempty_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "docs-demo"
            output_dir.mkdir()
            (output_dir / "keep.txt").write_text("preserve me", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(GENERATOR), "--output-dir", str(output_dir)],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("must be empty", result.stderr)
            self.assertEqual((output_dir / "keep.txt").read_text(encoding="utf-8"), "preserve me")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_server(port: int, server: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if server.poll() is not None:
            output = server.stdout.read() if server.stdout else ""
            raise AssertionError(f"Demo server exited early.\n{output}")
        try:
            _get_json(port, "/api/health/live")
            return
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            time.sleep(0.1)
    raise AssertionError("Timed out waiting for the documentation demo server.")


def _get_json(port: int, path: str) -> dict | list:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
        return json.loads(response.read())


if __name__ == "__main__":
    unittest.main()
