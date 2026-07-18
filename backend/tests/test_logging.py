from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.logging_config import application_log_path, read_application_logs


class ApplicationLoggingTest(unittest.TestCase):
    def test_reads_latest_structured_entries_with_filters(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            logs_dir = Path(temp_dir)
            path = application_log_path(logs_dir)
            path.parent.mkdir(parents=True)
            entries = [
                {"timestamp": "2026-07-18T10:00:00+00:00", "level": "info", "logger": "media_atlas.startup", "message": "started"},
                {"timestamp": "2026-07-18T10:01:00+00:00", "level": "warning", "logger": "media_atlas.scan", "message": "slow probe", "job_id": 7},
                {"timestamp": "2026-07-18T10:02:00+00:00", "level": "error", "logger": "media_atlas.scan", "message": "probe failed", "job_id": 7},
            ]
            path.write_text(
                "not-json\n" + "\n".join(json.dumps(entry) for entry in entries) + "\n",
                encoding="utf-8",
            )

            latest = read_application_logs(logs_dir, limit=2)
            self.assertTrue(latest["truncated"])
            self.assertEqual([item["message"] for item in latest["items"]], ["slow probe", "probe failed"])

            filtered = read_application_logs(
                logs_dir,
                limit=10,
                level="ERROR",
                logger_prefix="MEDIA_ATLAS.SCAN",
                query="JOB_ID\": 7",
            )
            self.assertFalse(filtered["truncated"])
            self.assertEqual([item["message"] for item in filtered["items"]], ["probe failed"])

if __name__ == "__main__":
    unittest.main()
