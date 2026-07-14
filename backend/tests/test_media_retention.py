from __future__ import annotations

import asyncio
import sqlite3
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class FakeState:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.catalogs: dict[int, list[dict]] = {}
        self.files: dict[tuple[int, int], list[dict]] = {}
        self.history: list[dict] = []
        self.failed_connections: set[int] = set()
        self.deleted: list[tuple[str, int]] = []
        self.seerr_deleted: list[tuple[int, bool]] = []
        self.fail_reconcile = False
        self.delete_error: Exception | None = None
        self.arr_item_calls = 0
        self.fail_arr_item_after: int | None = None


class FakeRetentionClient:
    def __init__(self, connection: dict, state: FakeState) -> None:
        self.connection = connection
        self.state = state

    async def test(self) -> dict:
        return {"ok": True, "service_type": self.connection["service_type"]}

    async def seerr_requests(self) -> list[dict]:
        return list(self.state.requests)

    async def arr_catalog(self) -> list[dict]:
        if self.connection["id"] in self.state.failed_connections:
            raise RuntimeError("source unavailable")
        return list(self.state.catalogs.get(self.connection["id"], []))

    async def arr_files(self, service_item_id: int) -> list[dict]:
        return list(self.state.files.get((self.connection["id"], service_item_id), []))

    async def arr_item(self, service_item_id: int) -> dict | None:
        self.state.arr_item_calls += 1
        if self.state.fail_arr_item_after and self.state.arr_item_calls >= self.state.fail_arr_item_after:
            raise RuntimeError("state unavailable")
        return next(
            (item for item in self.state.catalogs.get(self.connection["id"], []) if item["id"] == service_item_id),
            None,
        )

    async def delete_arr_item(self, service_item_id: int) -> None:
        if self.state.delete_error:
            raise self.state.delete_error
        self.state.deleted.append((self.connection["service_type"], service_item_id))
        self.state.catalogs[self.connection["id"]] = [
            item for item in self.state.catalogs.get(self.connection["id"], []) if item["id"] != service_item_id
        ]

    async def mark_seerr_deleted(self, media_id: int, is_4k: bool) -> None:
        if self.state.fail_reconcile:
            raise RuntimeError("Seerr reconciliation unavailable")
        self.state.seerr_deleted.append((media_id, is_4k))


class FakePlexManager:
    async def sync_now(self) -> dict:
        return {"status": "succeeded"}


class MediaRetentionTest(unittest.TestCase):
    def setUp(self) -> None:
        from app import db
        from app.config import CONFIG

        self.db = db
        self.config = CONFIG
        self.original_database_path = CONFIG.database_path
        self.temp_dir = tempfile.TemporaryDirectory()
        object.__setattr__(CONFIG, "database_path", Path(self.temp_dir.name) / "retention.sqlite")
        db.init_db()

    def tearDown(self) -> None:
        object.__setattr__(self.config, "database_path", self.original_database_path)
        self.temp_dir.cleanup()

    def _create_source_data(self, state: FakeState, *, second_arr: bool = False) -> tuple[int, int]:
        from app.services.media_retention import create_connection
        from app.services.plex import save_settings as save_plex_settings

        save_plex_settings({
            "enabled": True,
            "server_url": "http://plex.test",
            "token": "plex-secret",
            "path_mappings": [],
        })
        create_connection({
            "service_type": "seerr",
            "name": "Seerr",
            "server_url": "http://seerr.test",
            "api_key": "seerr-secret",
        })
        arr = create_connection({
            "service_type": "sonarr",
            "name": "Sonarr Standard",
            "server_url": "http://sonarr.test",
            "api_key": "sonarr-secret",
            "seerr_service_id": 10,
            "path_mappings": [{"source_path_prefix": "/arr", "media_atlas_path_prefix": "/media"}],
        })
        if second_arr:
            create_connection({
                "service_type": "sonarr",
                "name": "Sonarr 4K",
                "server_url": "http://sonarr4k.test",
                "api_key": "sonarr-4k-secret",
                "seerr_service_id": 11,
                "path_mappings": [{"source_path_prefix": "/arr4k", "media_atlas_path_prefix": "/media4k"}],
            })
        now = self.db.utc_now()
        root_id = self.db.execute(
            """
            INSERT INTO media_roots (
                name, path, enabled, include_extensions_json, exclude_patterns_json, created_at, updated_at
            ) VALUES ('TV', '/media', 1, '[\".mkv\"]', '[]', ?, ?)
            """,
            (now, now),
        )
        file_path = "/media/Example Show/Season 01/Episode 01.mkv"
        file_id = self.db.execute(
            """
            INSERT INTO files (
                root_id, path, directory, filename, extension, size_bytes, modified_time_ns,
                first_seen_at, last_seen_at, is_missing, audio_stream_count, subtitle_stream_count,
                video_stream_count, has_forced_subtitles, has_image_subtitles,
                recommendation_category, updated_at
            ) VALUES (?, ?, ?, 'Episode 01.mkv', '.mkv', 3000, 1, ?, ?, 0, 1, 0, 1, 0, 0, 'Easy Win', ?)
            """,
            (root_id, file_path, str(Path(file_path).parent), now, now, now),
        )
        self.db.execute(
            """
            INSERT INTO plex_items (
                rating_key, type, title, show_title, last_viewed_at, view_count,
                collections_json, genres_json, labels_json, raw_json, last_synced_at, is_stale
            ) VALUES ('plex-episode-1', 'episode', 'Pilot', 'Example Show', NULL, 0, '[]', '[]', '[]', '{}', ?, 0)
            """,
            (now,),
        )
        plex_item = self.db.query_one("SELECT id FROM plex_items WHERE rating_key = 'plex-episode-1'")
        self.db.execute(
            """
            INSERT INTO plex_media_parts (
                plex_item_id, part_id, file_path, normalized_path, size_bytes, last_synced_at
            ) VALUES (?, 'part-1', ?, ?, 3000, ?)
            """,
            (plex_item["id"], file_path, file_path, now),
        )

        old = datetime.now(UTC) - timedelta(days=120)
        later = old + timedelta(days=2)
        state.requests = [
            {
                "id": 1,
                "type": "tv",
                "status": 2,
                "serverId": 10,
                "createdAt": old.isoformat(),
                "requestedBy": {"displayName": "Alex"},
                "media": {"id": 501, "mediaType": "tv", "tmdbId": 42, "tvdbId": 84, "status": 5},
            },
            {
                "id": 2,
                "type": "tv",
                "status": 2,
                "serverId": 10,
                "createdAt": later.isoformat(),
                "requestedBy": {"displayName": "Bailey"},
                "media": {"id": 501, "mediaType": "tv", "tmdbId": 42, "tvdbId": 84, "status": 5},
            },
        ]
        state.catalogs[arr["id"]] = [{
            "id": 700,
            "title": "Example Show",
            "year": 2020,
            "tmdbId": 42,
            "tvdbId": 84,
            "path": "/arr/Example Show",
        }]
        state.files[(arr["id"], 700)] = [{
            "id": 900,
            "relativePath": "Season 01/Episode 01.mkv",
            "size": 3000,
            "dateAdded": old.isoformat(),
        }]
        return arr["id"], file_id

    def _run_analysis(self, state: FakeState):
        from app.services import media_retention as module

        class FakePlexClient:
            def __init__(self, settings: dict) -> None:
                self.settings = settings

            async def history(self, since: datetime) -> list[dict]:
                return list(state.history)

        async def scenario():
            manager = module.MediaRetentionManager(
                FakePlexManager(),
                client_factory=lambda connection, timeout: FakeRetentionClient(connection, state),
            )
            with patch.object(module, "PlexClient", FakePlexClient):
                job = await manager.start_analysis()
                await manager._task
                return module.read_analysis_job(job["id"])

        return asyncio.run(scenario())

    def test_analysis_aggregates_whole_series_and_plex_play_suppresses_candidate(self) -> None:
        from app.services.media_retention import candidate_export_rows, list_candidates, read_candidate

        state = FakeState()
        _, file_id = self._create_source_data(state)
        job = self._run_analysis(state)
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["candidate_count"], 1)
        page = list_candidates(status="active")
        self.assertEqual(page["total"], 1)
        candidate = read_candidate(page["items"][0]["id"])
        self.assertEqual(candidate["title"], "Example Show")
        self.assertEqual(candidate["requesters"], ["Alex", "Bailey"])
        self.assertEqual(candidate["size_bytes"], 3000)
        self.assertEqual(candidate["file_count"], 1)
        self.assertEqual(candidate["matched_file_count"], 1)
        self.assertEqual(candidate["files"][0]["media_atlas_file_id"], file_id)
        export = candidate_export_rows()
        self.assertEqual(export[0]["mapping_coverage"], "1/1")
        self.assertEqual(export[0]["requesters"], "Alex; Bailey")

        state.history = [{
            "historyKey": "history-1",
            "ratingKey": "plex-episode-1",
            "viewedAt": int(datetime.now(UTC).timestamp()),
            "accountID": 99,
            "type": "episode",
            "title": "Pilot",
        }]
        protected_job = self._run_analysis(state)
        self.assertEqual(protected_job["candidate_count"], 0)
        self.assertEqual(list_candidates(status="active")["total"], 0)

    def test_boundaries_mapping_instance_selection_pagination_and_redaction(self) -> None:
        from app.services import media_retention as module

        state = FakeState()
        arr_id, _ = self._create_source_data(state, second_arr=True)
        connections = module.list_connections()
        text = str(connections)
        self.assertNotIn("seerr-secret", text)
        self.assertNotIn("sonarr-secret", text)
        self.assertTrue(all("api_key_configured" in item for item in connections))

        standard = module.read_connection(arr_id, include_secret=True)
        standard["service_type_connection_count"] = 2
        standard_requests = module._requests_for_connection(state.requests, standard)
        self.assertEqual(len(standard_requests), 2)
        four_k = next(item for item in module.list_connections(include_secret=True) if item["name"] == "Sonarr 4K")
        four_k["service_type_connection_count"] = 2
        self.assertEqual(module._requests_for_connection(state.requests, four_k), [])

        old = datetime.now(UTC) - timedelta(days=90, seconds=1)
        recent = datetime.now(UTC) - timedelta(days=89)
        item = state.catalogs[arr_id][0]
        request = [{**state.requests[0], "createdAt": old.isoformat()}]
        file_row = [{**state.files[(arr_id, 700)][0], "dateAdded": old.isoformat()}]
        self.assertIsNotNone(module._build_subject(standard, item, file_row, request, 90))
        request[0]["createdAt"] = recent.isoformat()
        self.assertIsNone(module._build_subject(standard, item, file_row, request, 90))
        self.assertEqual(
            module._apply_arr_mappings("/arr/Example Show/a.mkv", standard["path_mappings"]),
            "/media/Example Show/a.mkv",
        )

        connection = {
            "id": 999,
            "name": "Seerr",
            "service_type": "seerr",
            "server_url": "http://seerr",
            "api_key": "secret",
        }
        client = module.RetentionApiClient(connection, 10)
        page_one = {"results": [{"id": index} for index in range(100)], "pageInfo": {"results": 101}}
        page_two = {"results": [{"id": 100}], "pageInfo": {"results": 101}}
        client.request = AsyncMock(side_effect=[page_one, page_two])
        result = asyncio.run(client.seerr_requests())
        self.assertEqual(len(result), 101)
        self.assertEqual(client.request.await_count, 2)

    def test_failed_arr_is_excluded_and_incomplete_mapping_is_diagnostic(self) -> None:
        from app.services.media_retention import list_candidates, list_connections

        state = FakeState()
        self._create_source_data(state, second_arr=True)
        four_k = next(item for item in list_connections(include_secret=True) if item["name"] == "Sonarr 4K")
        old = datetime.now(UTC) - timedelta(days=130)
        state.requests.append({
            "id": 3,
            "type": "tv",
            "status": 2,
            "serverId": 11,
            "is4k": True,
            "createdAt": old.isoformat(),
            "requestedBy": {"displayName": "Casey"},
            "media": {"id": 502, "mediaType": "tv", "tvdbId": 85, "status4k": 5},
        })
        state.catalogs[four_k["id"]] = [{"id": 701, "title": "Other Show", "tvdbId": 85, "path": "/arr4k/Other Show"}]
        state.files[(four_k["id"], 701)] = [{"id": 901, "relativePath": "Episode.mkv", "size": 9000, "dateAdded": old.isoformat()}]
        first = self._run_analysis(state)
        self.assertEqual(first["diagnostic_count"], 1)
        self.assertEqual(list_candidates(status="diagnostic")["total"], 1)
        state.failed_connections.add(four_k["id"])
        second = self._run_analysis(state)
        self.assertEqual(second["status"], "succeeded_with_warnings")
        self.assertEqual(second["diagnostic_count"], 0)
        self.assertEqual(list_candidates(status="diagnostic")["total"], 0)

    def test_transcode_handoff_delete_guards_parameters_and_audit(self) -> None:
        from app.services import media_retention as module

        state = FakeState()
        self._create_source_data(state)
        self._run_analysis(state)
        candidate = module.list_candidates(status="active")["items"][0]
        plan_result = module.create_candidate_transcode_plan(candidate["id"], 1, None, None, "tester")
        self.assertEqual(len(plan_result["plan"]["items"]), 1)
        completed_action = module.list_actions(10, candidate["id"])[0]
        self.assertEqual(completed_action["action_type"], "transcode_plan")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "UPDATE retention_actions SET status = 'tampered' WHERE id = ?",
                (completed_action["id"],),
            )

        async def wrong_confirmation():
            await module.delete_candidate(
                candidate["id"], "DELETE WRONG", "tester",
                client_factory=lambda connection, timeout: FakeRetentionClient(connection, state),
            )
        with self.assertRaises(module.MediaRetentionError):
            asyncio.run(wrong_confirmation())
        self.assertEqual(state.deleted, [])

        state.files[(next(key[0] for key in state.files), 700)][0]["size"] = 4000
        async def changed_files():
            with patch.object(module, "PlexClient", _plex_client_for(state)):
                await module.delete_candidate(
                    candidate["id"], "DELETE Example Show", "tester",
                    client_factory=lambda connection, timeout: FakeRetentionClient(connection, state),
                )
        with self.assertRaises(module.MediaRetentionError):
            asyncio.run(changed_files())
        self.assertEqual(state.deleted, [])
        state.files[(next(key[0] for key in state.files), 700)][0]["size"] = 3000

        state.fail_reconcile = True
        async def successful_delete_with_warning():
            with patch.object(module, "PlexClient", _plex_client_for(state)):
                return await module.delete_candidate(
                    candidate["id"], "DELETE Example Show", "tester",
                    client_factory=lambda connection, timeout: FakeRetentionClient(connection, state),
                )
        action = asyncio.run(successful_delete_with_warning())
        self.assertEqual(action["status"], "succeeded_with_warning")
        self.assertEqual(state.deleted, [("sonarr", 700)])
        self.assertEqual(module.read_candidate(candidate["id"])["action_state"], "deleted")
        state.fail_reconcile = False
        retry = asyncio.run(module.retry_seerr_reconciliation(
            action["id"], "tester", client_factory=lambda connection, timeout: FakeRetentionClient(connection, state)
        ))
        self.assertEqual(retry["status"], "succeeded")
        self.assertEqual(state.seerr_deleted, [(501, False)])

        sonarr = {
            "id": 1,
            "name": "Sonarr",
            "service_type": "sonarr",
            "server_url": "http://sonarr",
            "api_key": "secret",
        }
        direct = module.RetentionApiClient(sonarr, 10)
        direct.request = AsyncMock(return_value={})
        asyncio.run(direct.delete_arr_item(700))
        _, path = direct.request.await_args.args
        self.assertEqual(path, "/api/v3/series/700")
        self.assertEqual(direct.request.await_args.kwargs["params"], {
            "deleteFiles": "true",
            "addImportListExclusion": "false",
        })
        seerr = module.RetentionApiClient({**sonarr, "service_type": "seerr", "name": "Seerr"}, 10)
        seerr.request = AsyncMock(return_value={})
        asyncio.run(seerr.mark_seerr_deleted(501, False))
        self.assertEqual(seerr.request.await_args.args[1], "/api/v1/media/501/deleted")
        self.assertNotIn("clear", str(seerr.request.await_args).lower())

        radarr = module.RetentionApiClient({**sonarr, "service_type": "radarr", "name": "Radarr"}, 10)
        radarr.request = AsyncMock(return_value={})
        asyncio.run(radarr.delete_arr_item(800))
        self.assertEqual(radarr.request.await_args.args[1], "/api/v3/movie/800")
        self.assertEqual(radarr.request.await_args.kwargs["params"], {
            "deleteFiles": "true",
            "addImportExclusion": "false",
        })

    def test_fresh_play_and_ambiguous_timeout_block_automatic_retry(self) -> None:
        import httpx
        from app.services import media_retention as module

        state = FakeState()
        self._create_source_data(state)
        self._run_analysis(state)
        candidate = module.list_candidates(status="active")["items"][0]
        state.history = [{
            "historyKey": "fresh-play",
            "ratingKey": "plex-episode-1",
            "viewedAt": int(datetime.now(UTC).timestamp()),
            "accountID": 1,
        }]

        async def fresh_play_delete():
            with patch.object(module, "PlexClient", _plex_client_for(state)):
                await module.delete_candidate(
                    candidate["id"], "DELETE Example Show", "tester",
                    client_factory=lambda connection, timeout: FakeRetentionClient(connection, state),
                )

        with self.assertRaises(module.MediaRetentionError):
            asyncio.run(fresh_play_delete())
        self.assertEqual(state.deleted, [])
        self.assertEqual(module.list_actions(1, candidate["id"])[0]["status"], "failed")

        state.history = []
        state.delete_error = httpx.TimeoutException("ambiguous timeout")
        state.arr_item_calls = 0
        state.fail_arr_item_after = 2

        async def ambiguous_delete():
            with patch.object(module, "PlexClient", _plex_client_for(state)):
                await module.delete_candidate(
                    candidate["id"], "DELETE Example Show", "tester",
                    client_factory=lambda connection, timeout: FakeRetentionClient(connection, state),
                )

        with self.assertRaises(module.AmbiguousDeleteError):
            asyncio.run(ambiguous_delete())
        self.assertEqual(module.read_candidate(candidate["id"])["action_state"], "unknown")
        self.assertEqual(module.list_actions(1, candidate["id"])[0]["status"], "unknown")

    def test_restart_recovery_marks_analysis_interrupted_and_schedule_defaults_off(self) -> None:
        from app.services import media_retention as module

        self.assertFalse(module.get_settings()["schedule_enabled"])
        job_id = self.db.execute(
            """
            INSERT INTO retention_analysis_jobs (status, trigger_type, created_at)
            VALUES ('running', 'scheduled', ?)
            """,
            (self.db.utc_now(),),
        )

        async def recover():
            manager = module.MediaRetentionManager(FakePlexManager())
            await manager.recover_startup_jobs()
            manager._schedule_task.cancel()
            try:
                await manager._schedule_task
            except asyncio.CancelledError:
                pass

        asyncio.run(recover())
        self.assertEqual(module.read_analysis_job(job_id)["status"], "interrupted")

    def test_migration_upgrades_existing_0007_database_without_losing_inventory(self) -> None:
        upgrade_path = Path(self.temp_dir.name) / "upgrade-from-0007.sqlite"
        object.__setattr__(self.config, "database_path", upgrade_path)
        with self.db.connect() as connection:
            connection.execute(
                "CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            for version, script in self.db.MIGRATIONS[:7]:
                connection.executescript(script)
                connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                    (version, self.db.utc_now()),
                )
            connection.execute(
                """
                INSERT INTO media_roots (
                    name, path, enabled, include_extensions_json, exclude_patterns_json, created_at, updated_at
                ) VALUES ('Existing', '/media/existing', 1, '[]', '[]', ?, ?)
                """,
                (self.db.utc_now(), self.db.utc_now()),
            )
        self.db.init_db()
        self.assertIn("0008_media_retention_review", self.db.migration_status()["applied"])
        self.assertEqual(self.db.query_one("SELECT name FROM media_roots")["name"], "Existing")
        self.assertIsNotNone(self.db.query_one(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'retention_candidates'"
        ))


def _plex_client_for(state: FakeState):
    class FakePlexClient:
        def __init__(self, settings: dict) -> None:
            self.settings = settings

        async def history(self, since: datetime) -> list[dict]:
            return list(state.history)

    return FakePlexClient


if __name__ == "__main__":
    unittest.main()
