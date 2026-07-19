from __future__ import annotations

import asyncio
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class BackgroundJobResponsivenessTest(unittest.IsolatedAsyncioTestCase):
    async def test_scan_and_plex_jobs_do_not_block_the_request_event_loop(self) -> None:
        from app.services.plex import PlexSyncManager
        from app.services.scanner import ScanManager

        for manager, start_name, run_name in (
            (ScanManager(), "_start_scan_task", "_run_scan"),
            (PlexSyncManager(), "_start_sync_task", "_run_sync"),
        ):
            release = threading.Event()
            watchdog_fired = threading.Event()

            def watchdog() -> None:
                if not release.wait(1):
                    watchdog_fired.set()
                    release.set()

            async def blocking_job(_job_id: int) -> None:
                release.wait()

            watchdog_thread = threading.Thread(target=watchdog, daemon=True)
            watchdog_thread.start()
            with patch.object(manager, run_name, AsyncMock(side_effect=blocking_job)):
                async def heartbeat() -> None:
                    await asyncio.sleep(0)

                heartbeat_task = asyncio.create_task(heartbeat())
                task = getattr(manager, start_name)(1)
                await heartbeat_task
                release.set()
                await task
            watchdog_thread.join(timeout=0.1)

            self.assertFalse(
                watchdog_fired.is_set(),
                f"{type(manager).__name__} prevented the request event loop heartbeat",
            )


if __name__ == "__main__":
    unittest.main()
