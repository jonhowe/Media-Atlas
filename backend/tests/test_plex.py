from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.plex import PlexClient


class PlexClientTest(unittest.TestCase):
    def test_tv_library_requests_episode_items_for_file_parts(self) -> None:
        client = PlexClient(
            {
                "server_url": "http://plex.test",
                "token": "test-token",
                "timeout_seconds": 10,
            }
        )
        client._request = AsyncMock(
            return_value={
                "MediaContainer": {
                    "Metadata": [
                        {
                            "type": "episode",
                            "title": "Pilot",
                            "Media": [{"Part": [{"file": "/tv/Example Show/S01E01.mkv"}]}],
                        }
                    ]
                }
            }
        )

        items = asyncio.run(client.library_items("7", "show"))

        self.assertEqual(items[0]["type"], "episode")
        client._request.assert_awaited_once()
        path, params = client._request.await_args.args
        self.assertEqual(path, "/library/sections/7/all")
        self.assertEqual(params["type"], "4")

    def test_movie_library_keeps_default_item_listing(self) -> None:
        client = PlexClient(
            {
                "server_url": "http://plex.test",
                "token": "test-token",
                "timeout_seconds": 10,
            }
        )
        client._request = AsyncMock(return_value={"MediaContainer": {"Metadata": []}})

        asyncio.run(client.library_items("3", "movie"))

        _, params = client._request.await_args.args
        self.assertNotIn("type", params)


if __name__ == "__main__":
    unittest.main()
