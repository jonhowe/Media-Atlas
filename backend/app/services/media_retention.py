from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from .. import db
from .plex import PlexClient, PlexError, PlexSyncManager, get_settings as get_plex_settings, normalize_path
from .transcodes import create_plan

SETTINGS_KEY = "media_retention_settings"
DEFAULT_SETTINGS: dict[str, Any] = {
    "minimum_unwatched_days": 90,
    "schedule_enabled": False,
    "schedule_time": "03:00",
    "timeout_seconds": 20,
}
AVAILABLE_STATUSES = {5, "5", "available", "AVAILABLE"}
DECLINED_STATUSES = {3, "3", "declined", "DECLINED"}
ACTIONABLE_REQUEST_STATUSES = {
    2, "2", "approved", "APPROVED",
    5, "5", "completed", "COMPLETED",
}
PLANNER_CATEGORIES = {"Easy Win", "Remux Only", "Review"}


class MediaRetentionError(Exception):
    pass


class RetentionSourceError(MediaRetentionError):
    pass


class AmbiguousDeleteError(MediaRetentionError):
    pass


class RetentionApiClient:
    def __init__(self, connection: dict[str, Any], timeout_seconds: int) -> None:
        self.connection = connection
        self.base_url = str(connection.get("server_url") or "").rstrip("/")
        self.api_key = str(connection.get("api_key") or "")
        self.timeout_seconds = max(1, int(timeout_seconds))
        if not self.base_url or not self.api_key:
            raise RetentionSourceError(f"{connection.get('name') or 'Source'} is not fully configured.")

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        allow_not_found: bool = False,
    ) -> Any:
        try:
            import httpx
        except ModuleNotFoundError as exc:
            raise RetentionSourceError("The httpx dependency is required for retention integrations.") from exc
        headers = {"Accept": "application/json", "X-Api-Key": self.api_key}
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, headers=headers) as client:
                response = await client.request(
                    method,
                    f"{self.base_url}{path}",
                    params=params,
                    json=json_body,
                )
        except httpx.TimeoutException:
            raise
        except httpx.HTTPError as exc:
            raise RetentionSourceError(f"{self.connection['name']} request failed: {exc}") from exc
        if allow_not_found and response.status_code == 404:
            return None
        if response.status_code in {401, 403}:
            raise RetentionSourceError(f"{self.connection['name']} rejected the configured API key.")
        if response.status_code >= 400:
            detail = response.text.strip()[:300]
            suffix = f": {detail}" if detail else ""
            raise RetentionSourceError(
                f"{self.connection['name']} returned HTTP {response.status_code}{suffix}"
            )
        if response.status_code == 204 or not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise RetentionSourceError(f"{self.connection['name']} returned invalid JSON.") from exc

    async def test(self) -> dict[str, Any]:
        service = self.connection["service_type"]
        path = "/api/v1/status" if service == "seerr" else "/api/v3/system/status"
        result = await self.request("GET", path)
        return {
            "ok": True,
            "service_type": service,
            "name": self.connection["name"],
            "version": result.get("version") if isinstance(result, dict) else None,
        }

    async def seerr_requests(self) -> list[dict[str, Any]]:
        requests: list[dict[str, Any]] = []
        take = 100
        skip = 0
        while True:
            payload = await self.request(
                "GET",
                "/api/v1/request",
                params={"take": take, "skip": skip, "filter": "all", "sort": "added"},
            )
            batch = _as_list(payload.get("results") if isinstance(payload, dict) else payload)
            requests.extend(item for item in batch if isinstance(item, dict))
            page_info = payload.get("pageInfo") if isinstance(payload, dict) else {}
            total = _as_int((page_info or {}).get("results"))
            skip += len(batch)
            if not batch or len(batch) < take or (total is not None and skip >= total):
                break
        return requests

    async def arr_catalog(self) -> list[dict[str, Any]]:
        path = "/api/v3/movie" if self.connection["service_type"] == "radarr" else "/api/v3/series"
        payload = await self.request("GET", path)
        return [item for item in _as_list(payload) if isinstance(item, dict)]

    async def arr_files(self, service_item_id: int) -> list[dict[str, Any]]:
        if self.connection["service_type"] == "radarr":
            path = "/api/v3/moviefile"
            params = {"movieId": service_item_id}
        else:
            path = "/api/v3/episodefile"
            params = {"seriesId": service_item_id}
        payload = await self.request("GET", path, params=params)
        return [item for item in _as_list(payload) if isinstance(item, dict)]

    async def arr_item(self, service_item_id: int) -> dict[str, Any] | None:
        noun = "movie" if self.connection["service_type"] == "radarr" else "series"
        payload = await self.request(
            "GET", f"/api/v3/{noun}/{service_item_id}", allow_not_found=True
        )
        return payload if isinstance(payload, dict) else None

    async def delete_arr_item(self, service_item_id: int) -> None:
        if self.connection["service_type"] == "radarr":
            path = f"/api/v3/movie/{service_item_id}"
            params = {"deleteFiles": "true", "addImportExclusion": "false"}
        else:
            path = f"/api/v3/series/{service_item_id}"
            params = {"deleteFiles": "true", "addImportListExclusion": "false"}
        await self.request("DELETE", path, params=params)

    async def mark_seerr_deleted(self, media_id: int, is_4k: bool) -> None:
        await self.request(
            "POST", f"/api/v1/media/{media_id}/deleted", json_body={"is4k": bool(is_4k)}
        )


ClientFactory = Callable[[dict[str, Any], int], RetentionApiClient]


def get_settings() -> dict[str, Any]:
    row = db.query_one("SELECT value_json FROM app_settings WHERE key = ?", (SETTINGS_KEY,))
    stored = db.loads_json(row.get("value_json") if row else None, {})
    result = {**DEFAULT_SETTINGS, **stored}
    result["minimum_unwatched_days"] = min(3650, max(1, int(result["minimum_unwatched_days"])))
    result["timeout_seconds"] = min(120, max(1, int(result["timeout_seconds"])))
    result["schedule_time"] = _clean_schedule_time(result.get("schedule_time"))
    result["schedule_enabled"] = bool(result.get("schedule_enabled"))
    return result


def save_settings(payload: dict[str, Any]) -> dict[str, Any]:
    settings = {**get_settings()}
    for key in DEFAULT_SETTINGS:
        if key in payload and payload[key] is not None:
            settings[key] = payload[key]
    settings["minimum_unwatched_days"] = min(3650, max(1, int(settings["minimum_unwatched_days"])))
    settings["timeout_seconds"] = min(120, max(1, int(settings["timeout_seconds"])))
    settings["schedule_time"] = _clean_schedule_time(settings.get("schedule_time"))
    settings["schedule_enabled"] = bool(settings.get("schedule_enabled"))
    db.execute(
        """
        INSERT INTO app_settings (key, value_json, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at
        """,
        (SETTINGS_KEY, db.dumps(settings), db.utc_now()),
    )
    return settings


def seed_environment_connections() -> None:
    seeds = [
        ("seerr", "Seerr", "MEDIA_ATLAS_SEERR_URL", "MEDIA_ATLAS_SEERR_API_KEY", None, None),
        ("sonarr", "Sonarr", "MEDIA_ATLAS_SONARR_URL", "MEDIA_ATLAS_SONARR_API_KEY",
         "MEDIA_ATLAS_SONARR_SEERR_SERVICE_ID", "MEDIA_ATLAS_SONARR_PATH_MAPPINGS"),
        ("radarr", "Radarr", "MEDIA_ATLAS_RADARR_URL", "MEDIA_ATLAS_RADARR_API_KEY",
         "MEDIA_ATLAS_RADARR_SEERR_SERVICE_ID", "MEDIA_ATLAS_RADARR_PATH_MAPPINGS"),
    ]
    for service_type, name, url_key, api_key, service_id_key, mappings_key in seeds:
        url = os.getenv(url_key, "").strip().rstrip("/")
        secret = os.getenv(api_key, "").strip()
        if not url or not secret:
            continue
        existing = db.query_one(
            "SELECT id FROM retention_connections WHERE service_type = ? ORDER BY id LIMIT 1",
            (service_type,),
        )
        if existing:
            continue
        now = db.utc_now()
        db.execute(
            """
            INSERT INTO retention_connections (
                service_type, name, server_url, api_key, enabled, seerr_service_id,
                path_mappings_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)
            """,
            (
                service_type,
                name,
                url,
                secret,
                _as_int(os.getenv(service_id_key, "")) if service_id_key else None,
                db.dumps(_env_path_mappings(os.getenv(mappings_key, "")) if mappings_key else []),
                now,
                now,
            ),
        )


def list_connections(include_secret: bool = False, enabled_only: bool = False) -> list[dict[str, Any]]:
    where = "WHERE enabled = 1" if enabled_only else ""
    rows = db.query_all(
        f"SELECT * FROM retention_connections {where} ORDER BY service_type, name, id"
    )
    return [_inflate_connection(row, include_secret) for row in rows]


def read_connection(connection_id: int, include_secret: bool = False) -> dict[str, Any] | None:
    row = db.query_one("SELECT * FROM retention_connections WHERE id = ?", (connection_id,))
    return _inflate_connection(row, include_secret) if row else None


def create_connection(payload: dict[str, Any]) -> dict[str, Any]:
    service_type = str(payload.get("service_type") or "").lower()
    if service_type not in {"seerr", "sonarr", "radarr"}:
        raise MediaRetentionError("Service type must be Seerr, Sonarr, or Radarr.")
    if service_type == "seerr" and db.query_one(
        "SELECT id FROM retention_connections WHERE service_type = 'seerr'"
    ):
        raise MediaRetentionError("Only one Seerr connection can be configured.")
    api_key = str(payload.get("api_key") or "").strip()
    server_url = str(payload.get("server_url") or "").strip().rstrip("/")
    name = str(payload.get("name") or service_type.title()).strip()
    if not name or not server_url or not api_key:
        raise MediaRetentionError("Name, server URL, and API key are required.")
    now = db.utc_now()
    connection_id = db.execute(
        """
        INSERT INTO retention_connections (
            service_type, name, server_url, api_key, enabled, seerr_service_id,
            path_mappings_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            service_type,
            name,
            server_url,
            api_key,
            1 if payload.get("enabled", True) else 0,
            _as_int(payload.get("seerr_service_id")),
            db.dumps(_clean_path_mappings(payload.get("path_mappings") or [])),
            now,
            now,
        ),
    )
    return read_connection(connection_id) or {"id": connection_id}


def update_connection(connection_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    existing = read_connection(connection_id, include_secret=True)
    if not existing:
        raise MediaRetentionError("Retention connection not found.")
    next_value = dict(existing)
    for key in ("name", "server_url", "enabled", "seerr_service_id", "path_mappings"):
        if key in payload and (payload[key] is not None or key == "seerr_service_id"):
            next_value[key] = payload[key]
    if payload.get("clear_api_key"):
        next_value["api_key"] = ""
    elif payload.get("api_key"):
        next_value["api_key"] = str(payload["api_key"]).strip()
    name = str(next_value.get("name") or "").strip()
    server_url = str(next_value.get("server_url") or "").strip().rstrip("/")
    if not name or not server_url:
        raise MediaRetentionError("Name and server URL are required.")
    db.execute(
        """
        UPDATE retention_connections SET
            name = ?, server_url = ?, api_key = ?, enabled = ?, seerr_service_id = ?,
            path_mappings_json = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            name,
            server_url,
            str(next_value.get("api_key") or ""),
            1 if next_value.get("enabled") else 0,
            _as_int(next_value.get("seerr_service_id")),
            db.dumps(_clean_path_mappings(next_value.get("path_mappings") or [])),
            db.utc_now(),
            connection_id,
        ),
    )
    return read_connection(connection_id) or {"id": connection_id}


def delete_connection(connection_id: int) -> None:
    if (
        db.query_one("SELECT id FROM retention_candidates WHERE connection_id = ? LIMIT 1", (connection_id,))
        or db.query_one("SELECT id FROM retention_review_items WHERE connection_id = ? LIMIT 1", (connection_id,))
    ):
        raise MediaRetentionError("Connections with analysis history cannot be deleted; disable it instead.")
    if not db.query_one("SELECT id FROM retention_connections WHERE id = ?", (connection_id,)):
        raise MediaRetentionError("Retention connection not found.")
    db.execute("DELETE FROM retention_connections WHERE id = ?", (connection_id,))


class MediaRetentionManager:
    def __init__(
        self,
        plex_manager: PlexSyncManager,
        client_factory: ClientFactory = RetentionApiClient,
    ) -> None:
        self.plex_manager = plex_manager
        self.client_factory = client_factory
        self._task: asyncio.Task[Any] | None = None
        self._schedule_task: asyncio.Task[Any] | None = None
        self._lock = asyncio.Lock()
        self._last_schedule_date: str | None = None

    async def recover_startup_jobs(self) -> None:
        seed_environment_connections()
        now = db.utc_now()
        db.execute(
            """
            UPDATE retention_analysis_jobs
            SET status = 'interrupted', finished_at = ?,
                message = 'Backend restarted while this retention analysis was active.',
                error_message = 'Analysis snapshots are published atomically; no partial snapshot was retained.'
            WHERE status IN ('queued', 'running')
            """,
            (now,),
        )
        if self._schedule_task is None or self._schedule_task.done():
            self._schedule_task = asyncio.create_task(self._schedule_loop())

    async def start_analysis(self, trigger_type: str = "manual") -> dict[str, Any]:
        async with self._lock:
            active = db.query_one(
                "SELECT * FROM retention_analysis_jobs WHERE status IN ('queued','running') ORDER BY id DESC LIMIT 1"
            )
            if active:
                return _inflate_job(active)
            job_id = db.execute(
                """
                INSERT INTO retention_analysis_jobs (
                    status, trigger_type, created_at, current_stage, message
                ) VALUES ('queued', ?, ?, 'queued', 'Retention analysis queued.')
                """,
                (trigger_type, db.utc_now()),
            )
            self._task = asyncio.create_task(self._run_analysis(job_id))
            return read_analysis_job(job_id) or {"id": job_id}

    async def retry_analysis(self, job_id: int) -> dict[str, Any]:
        if not read_analysis_job(job_id):
            raise MediaRetentionError("Retention analysis job not found.")
        return await self.start_analysis("retry")

    def cancel_analysis(self, job_id: int) -> None:
        if not read_analysis_job(job_id):
            raise MediaRetentionError("Retention analysis job not found.")
        db.execute(
            """
            UPDATE retention_analysis_jobs
            SET cancel_requested = 1, message = 'Cancellation requested.'
            WHERE id = ? AND status IN ('queued','running')
            """,
            (job_id,),
        )

    async def test_connection(self, connection_id: int) -> dict[str, Any]:
        connection = read_connection(connection_id, include_secret=True)
        if not connection:
            raise MediaRetentionError("Retention connection not found.")
        return await self.client_factory(connection, get_settings()["timeout_seconds"]).test()

    async def _run_analysis(self, job_id: int) -> None:
        _update_job(job_id, status="running", started_at=db.utc_now(), current_stage="validate", progress=2,
                    message="Validating retention sources.")
        try:
            settings = get_settings()
            connections = list_connections(include_secret=True, enabled_only=True)
            seerr = next((item for item in connections if item["service_type"] == "seerr"), None)
            arr_connections = [item for item in connections if item["service_type"] in {"sonarr", "radarr"}]
            if not seerr:
                raise MediaRetentionError("An enabled Seerr connection is required.")
            if not arr_connections:
                raise MediaRetentionError("At least one enabled Sonarr or Radarr connection is required.")
            plex_settings = get_plex_settings(include_secret=True)
            if not plex_settings.get("enabled") or not plex_settings.get("server_url") or not plex_settings.get("token"):
                raise MediaRetentionError("An enabled Plex connection is required.")
            _check_canceled(job_id)

            _update_job(job_id, current_stage="plex", progress=8, message="Refreshing Plex libraries and parts.")
            await self.plex_manager.sync_now()
            _check_canceled(job_id)

            _update_job(job_id, current_stage="seerr", progress=22, message="Reading Seerr request history.")
            seerr_client = self.client_factory(seerr, settings["timeout_seconds"])
            requests = await seerr_client.seerr_requests()
            _check_canceled(job_id)

            warnings: list[dict[str, Any]] = []
            candidates: list[dict[str, Any]] = []
            review_items: list[dict[str, Any]] = []
            routed_requests: set[str] = set()
            completed_connections = 0
            type_counts = {
                service_type: sum(1 for item in arr_connections if item["service_type"] == service_type)
                for service_type in ("sonarr", "radarr")
            }
            for connection in arr_connections:
                client = self.client_factory(connection, settings["timeout_seconds"])
                connection["service_type_connection_count"] = type_counts[connection["service_type"]]
                connection_requests = _requests_for_connection(
                    requests, connection, require_available=False, include_declined=True
                )
                routed_requests.update(_request_identity(item) for item in connection_requests)
                try:
                    catalog = await client.arr_catalog()
                    grouped = _match_requests_to_catalog(connection_requests, catalog, connection)
                    matched_requests: set[str] = set()
                    for item, item_requests in grouped:
                        _check_canceled(job_id)
                        files = await client.arr_files(int(item["id"]))
                        matched_requests.update(_request_identity(request) for request in item_requests)
                        review_item = _build_review_item(
                            connection,
                            item,
                            files,
                            item_requests,
                        )
                        review_items.append(review_item)
                        deletion_requests = _requests_for_connection(item_requests, connection)
                        deletion_subject = _build_subject(
                            connection,
                            item,
                            files,
                            deletion_requests,
                            int(settings["minimum_unwatched_days"]),
                        ) if deletion_requests else None
                        candidate = deletion_subject or _build_review_candidate(
                            connection, item, review_item["managed_files"], item_requests
                        )
                        if candidate:
                            candidate["candidate_key"] = review_item["candidate_key"]
                            candidate["deletion_gate_passed"] = deletion_subject is not None
                            candidates.append(candidate)
                    for request in connection_requests:
                        if _request_identity(request) not in matched_requests:
                            review_items.append(_unmatched_review_item(
                                request,
                                connection,
                                "arr_not_matched",
                                "The request could not be matched to the configured Arr catalog.",
                            ))
                except asyncio.CancelledError:
                    raise
                except _AnalysisCanceled:
                    raise
                except Exception as exc:
                    warnings.append(
                        {
                            "source": connection["name"],
                            "connection_id": connection["id"],
                            "message": str(exc),
                        }
                    )
                    for request in connection_requests:
                        review_items.append(_unmatched_review_item(
                            request,
                            connection,
                            "source_unavailable",
                            f"{connection['name']} could not be evaluated: {exc}",
                        ))
                finally:
                    completed_connections += 1
                progress = 22 + int(38 * completed_connections / max(1, len(arr_connections)))
                _update_job(job_id, current_stage="arr", progress=progress,
                            message=f"Read {completed_connections} of {len(arr_connections)} Arr instances.",
                            warnings=warnings)
            _check_canceled(job_id)

            for request in requests:
                if _request_identity(request) in routed_requests:
                    continue
                request_type = _request_media_type(request)
                if request_type not in {"movie", "tv"}:
                    continue
                review_items.append(_unmatched_review_item(
                    request,
                    None,
                    "instance_unresolved",
                    "The request could not be routed to one enabled Arr instance. Check Seerr service IDs.",
                ))

            _update_job(job_id, current_stage="mapping", progress=65, message="Matching managed files to Plex parts.")
            await asyncio.to_thread(_map_analysis_files, review_items, candidates)
            _check_canceled(job_id)

            eligibility_dates = [
                parsed
                for item in review_items
                for scope in item.get("scopes", [])
                for file_row in scope.get("files", [])
                if (parsed := _parse_datetime(file_row.get("eligible_since"), required=False))
            ]
            eligibility_dates.extend(
                parsed for item in candidates
                if (parsed := _parse_datetime(item.get("eligible_since"), required=False))
            )
            earliest = min(eligibility_dates, default=None)
            history: list[dict[str, Any]] = []
            if earliest:
                _update_job(job_id, current_stage="history", progress=76, message="Reading Plex play history.")
                history = await PlexClient(plex_settings).history(earliest)
            watch_rows = await asyncio.to_thread(
                _apply_history_evidence,
                job_id,
                history,
                candidates,
                review_items,
                int(settings["minimum_unwatched_days"]),
            )
            _check_canceled(job_id)

            _update_job(job_id, current_stage="publish", progress=92, message="Publishing retention snapshot.")
            await asyncio.to_thread(_publish_snapshot, job_id, candidates, review_items, watch_rows, warnings)
            status = "succeeded_with_warnings" if warnings else "succeeded"
            _update_job(
                job_id,
                status=status,
                finished_at=db.utc_now(),
                current_stage="complete",
                progress=100,
                message="Retention analysis completed." if not warnings else "Retention analysis completed with source warnings.",
                warnings=warnings,
            )
        except _AnalysisCanceled:
            _update_job(job_id, status="canceled", finished_at=db.utc_now(), current_stage="canceled",
                        message="Retention analysis canceled.")
        except (PlexError, MediaRetentionError, RetentionSourceError) as exc:
            _update_job(job_id, status="failed", finished_at=db.utc_now(), current_stage="failed",
                        message="Retention analysis failed.", error=str(exc))
        except Exception as exc:
            _update_job(job_id, status="failed", finished_at=db.utc_now(), current_stage="failed",
                        message="Retention analysis failed.", error=str(exc))

    async def _schedule_loop(self) -> None:
        while True:
            try:
                settings = get_settings()
                now = datetime.now().astimezone()
                schedule_date = now.date().isoformat()
                if settings["schedule_enabled"] and now.strftime("%H:%M") == settings["schedule_time"]:
                    if self._last_schedule_date != schedule_date and not _scheduled_run_exists(schedule_date):
                        self._last_schedule_date = schedule_date
                        await self.start_analysis("scheduled")
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                return
            except Exception:
                await asyncio.sleep(30)


def read_analysis_job(job_id: int) -> dict[str, Any] | None:
    row = db.query_one("SELECT * FROM retention_analysis_jobs WHERE id = ?", (job_id,))
    return _inflate_job(row) if row else None


def list_analysis_jobs(limit: int = 30) -> list[dict[str, Any]]:
    return [_inflate_job(row) for row in db.query_all(
        "SELECT * FROM retention_analysis_jobs ORDER BY id DESC LIMIT ?", (limit,)
    )]


def latest_snapshot_job_id() -> int | None:
    row = db.query_one(
        """
        SELECT id FROM retention_analysis_jobs
        WHERE status IN ('succeeded','succeeded_with_warnings')
        ORDER BY id DESC LIMIT 1
        """
    )
    return int(row["id"]) if row else None


def retention_summary() -> dict[str, Any]:
    latest = db.query_one("SELECT * FROM retention_analysis_jobs ORDER BY id DESC LIMIT 1")
    snapshot_id = latest_snapshot_job_id()
    counts = {
        "candidate_count": 0,
        "diagnostic_count": 0,
        "total_size_bytes": 0,
        "evaluated_title_count": 0,
        "review_ready_scope_count": 0,
        "waiting_scope_count": 0,
        "protected_scope_count": 0,
        "attention_scope_count": 0,
        "review_ready_size_bytes": 0,
    }
    if snapshot_id:
        counts = db.query_one(
            """
            SELECT
              SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS candidate_count,
              SUM(CASE WHEN status = 'diagnostic' THEN 1 ELSE 0 END) AS diagnostic_count,
              COALESCE(SUM(CASE WHEN status = 'active' THEN size_bytes ELSE 0 END), 0) AS total_size_bytes,
              (SELECT evaluated_title_count FROM retention_analysis_jobs WHERE id = ?) AS evaluated_title_count,
              (SELECT review_ready_scope_count FROM retention_analysis_jobs WHERE id = ?) AS review_ready_scope_count,
              (SELECT waiting_scope_count FROM retention_analysis_jobs WHERE id = ?) AS waiting_scope_count,
              (SELECT protected_scope_count FROM retention_analysis_jobs WHERE id = ?) AS protected_scope_count,
              (SELECT attention_scope_count FROM retention_analysis_jobs WHERE id = ?) AS attention_scope_count,
              (SELECT review_ready_size_bytes FROM retention_analysis_jobs WHERE id = ?) AS review_ready_size_bytes
            FROM retention_candidates WHERE analysis_job_id = ?
            """,
            (snapshot_id, snapshot_id, snapshot_id, snapshot_id, snapshot_id, snapshot_id, snapshot_id),
        ) or counts
    configured = list_connections()
    return {
        "candidate_count": int(counts.get("candidate_count") or 0),
        "diagnostic_count": int(counts.get("diagnostic_count") or 0),
        "total_size_bytes": int(counts.get("total_size_bytes") or 0),
        "evaluated_title_count": int(counts.get("evaluated_title_count") or 0),
        "review_ready_scope_count": int(counts.get("review_ready_scope_count") or 0),
        "waiting_scope_count": int(counts.get("waiting_scope_count") or 0),
        "protected_scope_count": int(counts.get("protected_scope_count") or 0),
        "attention_scope_count": int(counts.get("attention_scope_count") or 0),
        "review_ready_size_bytes": int(counts.get("review_ready_size_bytes") or 0),
        "latest_analysis": _inflate_job(latest) if latest else None,
        "snapshot_job_id": snapshot_id,
        "configured": bool(
            any(item["service_type"] == "seerr" and item["enabled"] for item in configured)
            and any(item["service_type"] in {"sonarr", "radarr"} and item["enabled"] for item in configured)
        ),
    }


def list_candidates(
    *,
    page: int = 1,
    page_size: int = 50,
    status: str | None = None,
    media_type: str | None = None,
    connection_id: int | None = None,
    query: str | None = None,
    sort: str = "size_bytes",
    direction: str = "desc",
) -> dict[str, Any]:
    job_id = latest_snapshot_job_id()
    if not job_id:
        return {"items": [], "total": 0, "page": page, "page_size": page_size, "snapshot_job_id": None}
    clauses = ["rc.analysis_job_id = ?"]
    params: list[Any] = [job_id]
    if status and status != "all":
        clauses.append("rc.status = ?")
        params.append(status)
    if media_type and media_type != "all":
        clauses.append("rc.media_type = ?")
        params.append(media_type)
    if connection_id:
        clauses.append("rc.connection_id = ?")
        params.append(connection_id)
    if query:
        clauses.append("LOWER(rc.title) LIKE ?")
        params.append(f"%{query.lower()}%")
    allowed_sorts = {
        "size_bytes": "rc.size_bytes",
        "title": "rc.title",
        "eligible_since": "rc.eligible_since",
        "file_count": "rc.file_count",
    }
    order = allowed_sorts.get(sort, "rc.size_bytes")
    order_direction = "ASC" if direction.lower() == "asc" else "DESC"
    where = " AND ".join(clauses)
    total = db.query_one(
        f"SELECT COUNT(*) AS count FROM retention_candidates rc WHERE {where}", tuple(params)
    ) or {"count": 0}
    offset = max(0, page - 1) * page_size
    rows = db.query_all(
        f"""
        SELECT rc.*, con.name AS connection_name, con.service_type
        FROM retention_candidates rc
        JOIN retention_connections con ON con.id = rc.connection_id
        WHERE {where}
        ORDER BY {order} {order_direction}, rc.id DESC
        LIMIT ? OFFSET ?
        """,
        tuple([*params, page_size, offset]),
    )
    return {
        "items": [_inflate_candidate(row, include_files=False) for row in rows],
        "total": int(total["count"]),
        "page": page,
        "page_size": page_size,
        "snapshot_job_id": job_id,
    }


def read_candidate(candidate_id: int) -> dict[str, Any] | None:
    row = db.query_one(
        """
        SELECT rc.*, con.name AS connection_name, con.service_type
        FROM retention_candidates rc
        JOIN retention_connections con ON con.id = rc.connection_id
        WHERE rc.id = ?
        """,
        (candidate_id,),
    )
    return _inflate_candidate(row, include_files=True) if row else None


def candidate_export_rows() -> list[dict[str, Any]]:
    result = list_candidates(page=1, page_size=100000, status="all")
    rows = []
    now = datetime.now(UTC)
    for item in result["items"]:
        if item["status"] not in {"active", "diagnostic"}:
            continue
        rows.append(
            {
                "title": item["title"],
                "year": item.get("year"),
                "media_type": item["media_type"],
                "instance": item["connection_name"],
                "service": item["service_type"],
                "requesters": "; ".join(item["requesters"]),
                "size_bytes": item["size_bytes"],
                "size_gib": round(item["size_bytes"] / (1024 ** 3), 3),
                "eligible_since": item["eligible_since"],
                "eligibility_age_days": max(0, (now - _parse_datetime(item["eligible_since"])).days),
                "file_count": item["file_count"],
                "matched_file_count": item["matched_file_count"],
                "mapping_coverage": f"{item['matched_file_count']}/{item['file_count']}",
                "zero_play_evidence": "No Plex play at or after eligibility date",
                "reason": item["reason"],
                "status": item["status"],
                "available_actions": "; ".join(item["available_actions"]),
            }
        )
    return rows


def list_review_results(
    *,
    page: int = 1,
    page_size: int = 50,
    decision: str | None = "review_ready",
    media_type: str | None = None,
    connection_id: int | None = None,
    query: str | None = None,
    sort: str = "total_size_bytes",
    direction: str = "desc",
) -> dict[str, Any]:
    job_id = latest_snapshot_job_id()
    if not job_id:
        return {"items": [], "total": 0, "page": page, "page_size": page_size, "snapshot_job_id": None}
    clauses = ["rri.analysis_job_id = ?"]
    params: list[Any] = [job_id]
    if decision and decision != "all":
        if decision == "deletion_eligible":
            clauses.append("rri.deletion_eligible = 1")
        else:
            clauses.append(
                "EXISTS (SELECT 1 FROM retention_review_scopes rrs "
                "WHERE rrs.review_item_id = rri.id AND rrs.decision = ?)"
            )
            params.append(decision)
    if media_type and media_type != "all":
        clauses.append("rri.media_type = ?")
        params.append(media_type)
    if connection_id:
        clauses.append("rri.connection_id = ?")
        params.append(connection_id)
    if query:
        clauses.append("LOWER(rri.title) LIKE ?")
        params.append(f"%{query.lower()}%")
    allowed_sorts = {
        "total_size_bytes": "rri.total_size_bytes",
        "title": "rri.title",
        "review_ready_file_count": "rri.review_ready_file_count",
        "total_file_count": "rri.total_file_count",
    }
    order = allowed_sorts.get(sort, "rri.total_size_bytes")
    order_direction = "ASC" if direction.lower() == "asc" else "DESC"
    where = " AND ".join(clauses)
    total = db.query_one(
        f"SELECT COUNT(*) AS count FROM retention_review_items rri WHERE {where}", tuple(params)
    ) or {"count": 0}
    offset = max(0, page - 1) * page_size
    rows = db.query_all(
        f"""
        SELECT rri.*, con.name AS connection_name, con.service_type
        FROM retention_review_items rri
        LEFT JOIN retention_connections con ON con.id = rri.connection_id
        WHERE {where}
        ORDER BY {order} {order_direction}, rri.id DESC
        LIMIT ? OFFSET ?
        """,
        tuple([*params, page_size, offset]),
    )
    return {
        "items": [_inflate_review_item(row, include_files=False) for row in rows],
        "total": int(total["count"]),
        "page": page,
        "page_size": page_size,
        "snapshot_job_id": job_id,
    }


def read_review_result(result_id: int) -> dict[str, Any] | None:
    row = db.query_one(
        """
        SELECT rri.*, con.name AS connection_name, con.service_type
        FROM retention_review_items rri
        LEFT JOIN retention_connections con ON con.id = rri.connection_id
        WHERE rri.id = ?
        """,
        (result_id,),
    )
    return _inflate_review_item(row, include_files=True) if row else None


def review_result_export_rows() -> list[dict[str, Any]]:
    result = list_review_results(page=1, page_size=100000, decision="all")
    rows: list[dict[str, Any]] = []
    for item in result["items"]:
        for scope in item["scopes"]:
            rows.append({
                "title": item["title"],
                "year": item.get("year"),
                "media_type": item["media_type"],
                "scope": (
                    f"Season {scope['season_number']}"
                    if scope["scope_type"] == "season"
                    else scope["scope_type"].title()
                ),
                "instance": item.get("connection_name") or "Unresolved",
                "decision": scope["decision"],
                "reason": scope["reason"],
                "requesters": "; ".join(item["requesters"]),
                "latest_request_at": scope.get("latest_request_at"),
                "size_bytes": scope["total_size_bytes"],
                "size_gib": round(int(scope["total_size_bytes"]) / (1024 ** 3), 3),
                "file_count": scope["file_count"],
                "review_ready_file_count": scope["review_ready_file_count"],
                "waiting_file_count": scope["waiting_file_count"],
                "protected_file_count": scope["protected_file_count"],
                "attention_file_count": scope["attention_file_count"],
                "planning_eligible_file_count": scope["planning_eligible_file_count"],
                "deletion_eligible": "yes" if item["deletion_eligible"] else "no",
            })
    return rows


def create_review_scope_transcode_plan(
    result_id: int,
    scope_id: int,
    profile_id: int,
    file_ids: list[int] | None,
    name: str | None,
    requested_by: str | None,
) -> dict[str, Any]:
    scope = db.query_one(
        """
        SELECT rrs.*, rri.candidate_id, rri.title
        FROM retention_review_scopes rrs
        JOIN retention_review_items rri ON rri.id = rrs.review_item_id
        WHERE rri.id = ? AND rrs.id = ?
        """,
        (result_id, scope_id),
    )
    if not scope:
        raise MediaRetentionError("Retention review scope not found.")
    if scope["decision"] != "review_ready" or not scope.get("candidate_id"):
        raise MediaRetentionError("This review scope is not available for transcode planning.")
    eligible_rows = db.query_all(
        """
        SELECT DISTINCT rrf.media_atlas_file_id
        FROM retention_review_files rrf
        JOIN files f ON f.id = rrf.media_atlas_file_id
        WHERE rrf.review_scope_id = ? AND rrf.planning_eligible = 1
          AND f.is_missing = 0
          AND f.recommendation_category IN ('Easy Win', 'Remux Only', 'Review')
        ORDER BY rrf.media_atlas_file_id
        """,
        (scope_id,),
    )
    eligible = [int(row["media_atlas_file_id"]) for row in eligible_rows]
    selected = sorted(set(file_ids or eligible))
    if not selected or not set(selected).issubset(set(eligible)):
        raise MediaRetentionError("One or more selected files are not planning-eligible in this review scope.")
    scope_label = (
        f" S{int(scope['season_number']):02d}"
        if scope.get("scope_type") == "season" and scope.get("season_number") is not None
        else ""
    )
    return create_candidate_transcode_plan(
        int(scope["candidate_id"]),
        profile_id,
        selected,
        name or f"Retention review: {scope['title']}{scope_label}",
        requested_by,
        review_scope_id=scope_id,
    )


def list_actions(limit: int = 100, candidate_id: int | None = None) -> list[dict[str, Any]]:
    params: tuple[Any, ...]
    where = ""
    if candidate_id:
        where = "WHERE ra.candidate_id = ?"
        params = (candidate_id, limit)
    else:
        params = (limit,)
    rows = db.query_all(
        f"""
        SELECT ra.*, rc.title, rc.media_type, con.name AS connection_name
        FROM retention_actions ra
        JOIN retention_candidates rc ON rc.id = ra.candidate_id
        JOIN retention_connections con ON con.id = rc.connection_id
        {where}
        ORDER BY ra.id DESC LIMIT ?
        """,
        params,
    )
    for row in rows:
        row["snapshot"] = db.loads_json(row.pop("snapshot_json", None), {})
        row["result"] = db.loads_json(row.pop("result_json", None), None)
    return rows


def create_candidate_transcode_plan(
    candidate_id: int,
    profile_id: int,
    file_ids: list[int] | None,
    name: str | None,
    requested_by: str | None,
    *,
    review_scope_id: int | None = None,
) -> dict[str, Any]:
    candidate = read_candidate(candidate_id)
    if not candidate:
        raise MediaRetentionError("Retention candidate not found.")
    if candidate["status"] == "review_only" and review_scope_id is None:
        raise MediaRetentionError("Review-only files must be planned from their eligible retention scope.")
    available = sorted({
        int(item["media_atlas_file_id"])
        for item in candidate["files"]
        if item.get("media_atlas_file_id")
    })
    selected = sorted(set(file_ids or []))
    if selected and not set(selected).issubset(set(available)):
        raise MediaRetentionError("One or more selected files do not belong to this candidate.")
    if not selected:
        preferred = db.query_all(
            f"""
            SELECT id FROM files
            WHERE id IN ({','.join('?' for _ in available)})
              AND recommendation_category IN ('Easy Win','Remux Only','Review')
            ORDER BY id
            """,
            tuple(available),
        ) if available else []
        selected = [int(item["id"]) for item in preferred] or available
    if not selected:
        raise MediaRetentionError("No candidate files are matched to the Media Atlas inventory.")
    action_id = _create_action(
        candidate,
        "transcode_plan",
        requested_by,
        "running",
        {"review_scope_id": review_scope_id} if review_scope_id else None,
    )
    try:
        plan = create_plan(
            name or f"Retention review: {candidate['title']}",
            profile_id,
            selected,
            f"Created from retention candidate {candidate_id}. No transcode was started.",
        )
        db.execute("UPDATE retention_actions SET transcode_plan_id = ? WHERE id = ?", (plan["id"], action_id))
        _finish_action(action_id, "succeeded", {"transcode_plan_id": plan["id"], "file_ids": selected})
        return {"action": list_actions(1, candidate_id)[0], "plan": plan}
    except Exception as exc:
        _finish_action(action_id, "failed", None, str(exc))
        raise MediaRetentionError(str(exc)) from exc


async def delete_candidate(
    candidate_id: int,
    confirmation_text: str,
    requested_by: str | None,
    client_factory: ClientFactory = RetentionApiClient,
    plex_manager: PlexSyncManager | None = None,
) -> dict[str, Any]:
    candidate = read_candidate(candidate_id)
    if not candidate:
        raise MediaRetentionError("Retention candidate not found.")
    if candidate["status"] != "active" or candidate.get("action_state"):
        raise MediaRetentionError("This candidate is not available for deletion.")
    expected = f"DELETE {candidate['title']}"
    if confirmation_text != expected:
        raise MediaRetentionError(f"Deletion requires the exact confirmation text: {expected}")
    action_id = _create_action(candidate, "delete", requested_by, "revalidating")
    connection = read_connection(int(candidate["connection_id"]), include_secret=True)
    seerr = next((item for item in list_connections(include_secret=True, enabled_only=True)
                  if item["service_type"] == "seerr"), None)
    if not connection or not seerr:
        _finish_action(action_id, "failed", None, "Owning Arr or Seerr connection is unavailable.")
        raise MediaRetentionError("Owning Arr and Seerr connections must be enabled before deletion.")
    timeout = get_settings()["timeout_seconds"]
    arr_client = client_factory(connection, timeout)
    seerr_client = client_factory(seerr, timeout)
    try:
        if plex_manager:
            await plex_manager.sync_now()
        revalidation = await _revalidate_candidate(candidate, arr_client, seerr_client)
        if not revalidation["ok"]:
            raise MediaRetentionError(str(revalidation["message"]))
        db.execute("UPDATE retention_actions SET status = 'running', started_at = ? WHERE id = ?",
                   (db.utc_now(), action_id))
        try:
            await arr_client.delete_arr_item(int(candidate["service_item_id"]))
        except Exception as exc:
            if not _is_timeout(exc):
                raise
            try:
                item = await arr_client.arr_item(int(candidate["service_item_id"]))
            except Exception as check_exc:
                message = (
                    "Arr deletion timed out and the title state could not be determined. "
                    "Inspect the owning service before taking another action."
                )
                _finish_action(action_id, "unknown", {"timeout": True}, f"{message} {check_exc}")
                db.execute("UPDATE retention_candidates SET action_state = 'unknown' WHERE id = ?", (candidate_id,))
                raise AmbiguousDeleteError(message) from check_exc
            if item is not None:
                raise MediaRetentionError("Arr deletion timed out and the title is still present; no retry was attempted.")
        reconciliation_warning: str | None = None
        if candidate.get("seerr_media_id"):
            try:
                await seerr_client.mark_seerr_deleted(
                    int(candidate["seerr_media_id"]), bool(candidate.get("is_4k"))
                )
            except Exception as exc:
                reconciliation_warning = str(exc)
        result = {
            "deleted_via": candidate["service_type"],
            "service_item_id": candidate["service_item_id"],
            "seerr_reconciled": reconciliation_warning is None,
            "warning": reconciliation_warning,
        }
        status = "succeeded_with_warning" if reconciliation_warning else "succeeded"
        _finish_action(action_id, status, result)
        db.execute("UPDATE retention_candidates SET action_state = 'deleted' WHERE id = ?", (candidate_id,))
        return list_actions(1, candidate_id)[0]
    except AmbiguousDeleteError:
        raise
    except Exception as exc:
        action = db.query_one("SELECT status FROM retention_actions WHERE id = ?", (action_id,))
        if action and action["status"] not in {"unknown", "succeeded", "succeeded_with_warning"}:
            _finish_action(action_id, "failed", None, str(exc))
        raise MediaRetentionError(str(exc)) from exc


async def retry_seerr_reconciliation(
    action_id: int,
    requested_by: str | None,
    client_factory: ClientFactory = RetentionApiClient,
) -> dict[str, Any]:
    original = db.query_one(
        """
        SELECT ra.*, rc.seerr_media_id, rc.is_4k, rc.id AS candidate_id
        FROM retention_actions ra JOIN retention_candidates rc ON rc.id = ra.candidate_id
        WHERE ra.id = ? AND ra.action_type = 'delete'
        """,
        (action_id,),
    )
    if not original or original["status"] != "succeeded_with_warning" or not original["seerr_media_id"]:
        raise MediaRetentionError("This action does not have a retryable Seerr reconciliation warning.")
    candidate = read_candidate(int(original["candidate_id"]))
    seerr = next((item for item in list_connections(include_secret=True, enabled_only=True)
                  if item["service_type"] == "seerr"), None)
    if not candidate or not seerr:
        raise MediaRetentionError("Seerr is not configured and enabled.")
    retry_id = _create_action(candidate, "seerr_reconcile", requested_by, "running")
    try:
        await client_factory(seerr, get_settings()["timeout_seconds"]).mark_seerr_deleted(
            int(original["seerr_media_id"]), bool(original["is_4k"])
        )
        _finish_action(retry_id, "succeeded", {"original_action_id": action_id})
        return list_actions(1, int(original["candidate_id"]))[0]
    except Exception as exc:
        _finish_action(retry_id, "failed", {"original_action_id": action_id}, str(exc))
        raise MediaRetentionError(str(exc)) from exc


async def _revalidate_candidate(
    candidate: dict[str, Any],
    arr_client: RetentionApiClient,
    seerr_client: RetentionApiClient,
) -> dict[str, Any]:
    current_item = await arr_client.arr_item(int(candidate["service_item_id"]))
    if not current_item:
        return {"ok": False, "message": "The title is no longer present in the owning Arr service."}
    requests = await seerr_client.seerr_requests()
    connection = read_connection(int(candidate["connection_id"]), include_secret=True)
    if not connection:
        return {"ok": False, "message": "The owning Arr connection no longer exists."}
    current_requests = _requests_for_connection(requests, connection)
    matches = _match_requests_to_catalog(current_requests, [current_item], connection)
    if not matches:
        return {"ok": False, "message": "Seerr no longer reports a qualifying request for this copy."}
    current_files = await arr_client.arr_files(int(candidate["service_item_id"]))
    subject = _build_subject(
        connection,
        current_item,
        current_files,
        matches[0][1],
        get_settings()["minimum_unwatched_days"],
    )
    if not subject:
        return {"ok": False, "message": "The copy no longer satisfies the age, availability, or file safeguards."}
    for item in subject["files"]:
        _map_file(item)
    if any(item["match_status"] != "matched" for item in subject["files"]):
        return {"ok": False, "message": "Fresh Plex mapping coverage is incomplete; deletion was blocked."}
    old_files = sorted((item["normalized_path"], int(item["size_bytes"])) for item in candidate["files"])
    new_files = sorted((item["normalized_path"], int(item["size_bytes"])) for item in subject["files"])
    if old_files != new_files or int(candidate["size_bytes"]) != int(subject["size_bytes"]):
        return {"ok": False, "message": "Managed files or disk sizes changed after analysis; deletion was blocked."}
    if candidate["eligible_since"] != subject["eligible_since"]:
        return {"ok": False, "message": "The eligibility date changed after analysis; deletion was blocked."}
    plex_settings = get_plex_settings(include_secret=True)
    history = await PlexClient(plex_settings).history(_parse_datetime(subject["eligible_since"]))
    if _subject_has_play(subject, _parse_datetime(subject["eligible_since"]), _history_index(history)):
        return {"ok": False, "message": "A qualifying Plex play was found during fresh revalidation."}
    return {"ok": True, "message": "Fresh source revalidation passed."}


def _requests_for_connection(
    requests: list[dict[str, Any]],
    connection: dict[str, Any],
    *,
    require_available: bool = True,
    include_declined: bool = False,
) -> list[dict[str, Any]]:
    media_type = "movie" if connection["service_type"] == "radarr" else "tv"
    service_id = _as_int(connection.get("seerr_service_id"))
    result = []
    for request in requests:
        media = request.get("media") or {}
        request_type = str(request.get("type") or media.get("mediaType") or "").lower()
        if request_type == "show":
            request_type = "tv"
        if request_type != media_type:
            continue
        if not include_declined and request.get("status") in DECLINED_STATUSES:
            continue
        is_4k = bool(request.get("is4k") or request.get("is_4k"))
        media_status = media.get("status4k") if is_4k else media.get("status")
        if require_available and media_status not in AVAILABLE_STATUSES:
            continue
        request_service_id = _as_int(request.get("serverId"))
        if request_service_id is None:
            key = "serviceId4k" if is_4k else "serviceId"
            request_service_id = _as_int(media.get(key))
        if service_id is not None and request_service_id is not None and service_id != request_service_id:
            continue
        if request_service_id is None and int(connection.get("service_type_connection_count") or 1) > 1:
            continue
        if (
            service_id is None
            and request_service_id is not None
            and int(connection.get("service_type_connection_count") or 1) > 1
        ):
            continue
        result.append(request)
    return result


def _match_requests_to_catalog(
    requests: list[dict[str, Any]], catalog: list[dict[str, Any]], connection: dict[str, Any]
) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    by_tmdb = {_as_int(item.get("tmdbId")): item for item in catalog if _as_int(item.get("tmdbId")) is not None}
    by_tvdb = {_as_int(item.get("tvdbId")): item for item in catalog if _as_int(item.get("tvdbId")) is not None}
    items: dict[int, dict[str, Any]] = {}
    for request in requests:
        media = request.get("media") or {}
        if connection["service_type"] == "radarr":
            item = by_tmdb.get(_as_int(media.get("tmdbId")))
        else:
            item = by_tvdb.get(_as_int(media.get("tvdbId"))) or by_tmdb.get(_as_int(media.get("tmdbId")))
        if not item or _as_int(item.get("id")) is None:
            continue
        item_id = int(item["id"])
        grouped[item_id].append(request)
        items[item_id] = item
    return [(items[item_id], grouped[item_id]) for item_id in sorted(grouped)]


def _request_media_type(request: dict[str, Any]) -> str:
    media = request.get("media") or {}
    value = str(request.get("type") or media.get("mediaType") or "").lower()
    return "tv" if value == "show" else value


def _request_identity(request: dict[str, Any]) -> str:
    request_id = _as_int(request.get("id"))
    if request_id is not None:
        return f"id:{request_id}"
    media = request.get("media") or {}
    return ":".join(
        [
            _request_media_type(request),
            str(_as_int(media.get("tmdbId")) or ""),
            str(_as_int(media.get("tvdbId")) or ""),
            "4k" if request.get("is4k") or request.get("is_4k") else "standard",
            str(request.get("createdAt") or request.get("created_at") or ""),
        ]
    )


def _request_is_actionable(status: Any) -> bool:
    return status in ACTIONABLE_REQUEST_STATUSES


def _season_number(value: dict[str, Any]) -> int | None:
    number = value.get("seasonNumber")
    if number is None:
        number = value.get("season_number")
    return _as_int(number)


def _request_payload(request: dict[str, Any]) -> dict[str, Any]:
    seasons = []
    for season in _as_list(request.get("seasons")):
        if isinstance(season, dict):
            number = _season_number(season)
            status = season.get("status")
            created_at = season.get("createdAt") or season.get("created_at")
        else:
            number = _as_int(season)
            status = None
            created_at = None
        if number is not None:
            seasons.append({
                "season_number": number,
                "status": status,
                "created_at": _iso(parsed) if (parsed := _parse_datetime(created_at, required=False)) else None,
            })
    created = _parse_datetime(request.get("createdAt") or request.get("created_at"), required=False)
    return {
        "id": _as_int(request.get("id")),
        "created_at": _iso(created) if created else None,
        "requester": _requester_name(request) or "Unknown requester",
        "is_4k": bool(request.get("is4k") or request.get("is_4k")),
        "status": request.get("status"),
        "seasons": seasons,
    }


def _review_title(request: dict[str, Any]) -> str:
    media = request.get("media") or {}
    title = request.get("title") or request.get("name") or media.get("title") or media.get("name")
    if title:
        return str(title)
    media_type = _request_media_type(request) or "media"
    tmdb_id = _as_int(media.get("tmdbId"))
    tvdb_id = _as_int(media.get("tvdbId"))
    identifier = f"TMDB {tmdb_id}" if tmdb_id is not None else f"TVDB {tvdb_id}" if tvdb_id is not None else _request_identity(request)
    return f"{media_type.upper()} {identifier}"


def _request_record(request: dict[str, Any], season: dict[str, Any] | None = None) -> dict[str, Any]:
    season_created = (season or {}).get("createdAt") or (season or {}).get("created_at")
    created = _parse_datetime(
        season_created or request.get("createdAt") or request.get("created_at"),
        required=False,
    )
    return {
        "request": request,
        "created": created,
        "status": (season or {}).get("status", request.get("status")),
    }


def _build_review_item(
    connection: dict[str, Any],
    item: dict[str, Any],
    files: list[dict[str, Any]],
    requests: list[dict[str, Any]],
) -> dict[str, Any]:
    managed = [_arr_file_payload(connection, item, file_row) for file_row in files]
    media = requests[0].get("media") or {} if requests else {}
    media_type = "movie" if connection["service_type"] == "radarr" else "tv"
    candidate_key = f"{connection['id']}:{int(item['id'])}"
    requesters = sorted(
        {name for name in (_requester_name(request) for request in requests) if name},
        key=str.casefold,
    ) or ["Unknown requester"]
    review_item: dict[str, Any] = {
        "result_key": f"matched:{candidate_key}",
        "candidate_key": candidate_key,
        "connection_id": int(connection["id"]),
        "service_item_id": int(item["id"]),
        "seerr_media_id": _as_int(media.get("id")),
        "media_type": media_type,
        "title": str(item.get("title") or item.get("sortTitle") or _review_title(requests[0])),
        "year": _as_int(item.get("year")),
        "tmdb_id": _as_int(item.get("tmdbId") or media.get("tmdbId")),
        "tvdb_id": _as_int(item.get("tvdbId") or media.get("tvdbId")),
        "is_4k": bool(any(request.get("is4k") or request.get("is_4k") for request in requests)),
        "requesters": requesters,
        "requests": [_request_payload(request) for request in requests],
        "raw_requests": requests,
        "managed_files": managed,
        "scopes": [],
    }
    if media_type == "movie":
        records = [_request_record(request) for request in requests]
        review_item["scopes"].append(_build_review_scope("movie", None, records, managed))
        return review_item

    season_records: dict[int, list[dict[str, Any]]] = defaultdict(list)
    legacy_records: list[dict[str, Any]] = []
    for request in requests:
        seasons = _as_list(request.get("seasons"))
        if not seasons:
            legacy_records.append(_request_record(request))
            continue
        found = False
        for season_value in seasons:
            season = season_value if isinstance(season_value, dict) else {"seasonNumber": season_value}
            season_number = _season_number(season)
            if season_number is None:
                continue
            found = True
            season_records[season_number].append(_request_record(request, season))
        if not found:
            legacy_records.append(_request_record(request))
    for season_number in sorted(season_records):
        season_files = [file_row for file_row in managed if file_row.get("season_number") == season_number]
        review_item["scopes"].append(
            _build_review_scope("season", season_number, season_records[season_number], season_files)
        )
    if legacy_records:
        review_item["scopes"].append({
            **_build_review_scope("series", None, legacy_records, []),
            "forced_decision": "needs_attention",
            "forced_reason": (
                "Seerr did not provide requested-season metadata, so Media Atlas did not guess which "
                "managed seasons belong to this request."
            ),
        })
    if not review_item["scopes"]:
        review_item["scopes"].append({
            **_build_review_scope("series", None, [], []),
            "forced_decision": "needs_attention",
            "forced_reason": "No requested-season metadata was available for this show.",
        })
    return review_item


def _build_review_scope(
    scope_type: str,
    season_number: int | None,
    records: list[dict[str, Any]],
    files: list[dict[str, Any]],
) -> dict[str, Any]:
    request_dates = [record["created"] for record in records if record.get("created")]
    latest_request = max(request_dates) if request_dates else None
    scope_files = []
    for file_row in files:
        file_date = _parse_datetime(file_row.get("date_added"), required=False)
        eligible = max(latest_request, file_date) if latest_request and file_date else None
        file_row["eligible_since"] = _iso(eligible) if eligible else None
        scope_files.append(file_row)
    return {
        "scope_type": scope_type,
        "season_number": season_number,
        "records": records,
        "request_actionable": any(_request_is_actionable(record.get("status")) for record in records),
        "latest_request_at": _iso(latest_request) if latest_request else None,
        "files": scope_files,
    }


def _build_review_candidate(
    connection: dict[str, Any],
    item: dict[str, Any],
    managed: list[dict[str, Any]],
    requests: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if (
        not managed
        or not requests
        or sum(file_row["size_bytes"] for file_row in managed) <= 0
        or any(not file_row["service_file_id"] or not file_row["path"] for file_row in managed)
    ):
        return None
    request_dates = [
        parsed for request in requests
        if (parsed := _parse_datetime(request.get("createdAt") or request.get("created_at"), required=False))
    ]
    file_dates = [
        parsed for file_row in managed
        if (parsed := _parse_datetime(file_row.get("date_added"), required=False))
    ]
    if not request_dates or len(file_dates) != len(managed):
        return None
    latest_request = max(request_dates)
    available_since = max(file_dates)
    eligible_since = max(latest_request, available_since)
    media = requests[0].get("media") or {}
    requesters = sorted(
        {name for name in (_requester_name(request) for request in requests) if name}, key=str.casefold
    ) or ["Unknown requester"]
    return {
        "connection_id": connection["id"],
        "service_item_id": int(item["id"]),
        "seerr_media_id": _as_int(media.get("id")),
        "media_type": "movie" if connection["service_type"] == "radarr" else "tv",
        "title": str(item.get("title") or item.get("sortTitle") or "Untitled"),
        "year": _as_int(item.get("year")),
        "tmdb_id": _as_int(item.get("tmdbId") or media.get("tmdbId")),
        "tvdb_id": _as_int(item.get("tvdbId") or media.get("tvdbId")),
        "is_4k": bool(any(request.get("is4k") or request.get("is_4k") for request in requests)),
        "size_bytes": sum(int(file_row["size_bytes"]) for file_row in managed),
        "file_count": len(managed),
        "matched_file_count": 0,
        "requesters": requesters,
        "requests": [_request_payload(request) for request in requests],
        "latest_request_at": _iso(latest_request),
        "available_since": _iso(available_since),
        "eligible_since": _iso(eligible_since),
        "files": managed,
    }


def _unmatched_review_item(
    request: dict[str, Any],
    connection: dict[str, Any] | None,
    reason_code: str,
    reason: str,
) -> dict[str, Any]:
    media = request.get("media") or {}
    media_type = _request_media_type(request)
    decision = "not_actionable" if not _request_is_actionable(request.get("status")) else "needs_attention"
    request_key = _request_identity(request)
    connection_key = connection["id"] if connection else "unrouted"
    return {
        "result_key": f"{reason_code}:{connection_key}:{request_key}",
        "candidate_key": None,
        "connection_id": int(connection["id"]) if connection else None,
        "service_item_id": None,
        "seerr_media_id": _as_int(media.get("id")),
        "media_type": media_type,
        "title": _review_title(request),
        "year": None,
        "tmdb_id": _as_int(media.get("tmdbId")),
        "tvdb_id": _as_int(media.get("tvdbId")),
        "is_4k": bool(request.get("is4k") or request.get("is_4k")),
        "requesters": [_requester_name(request) or "Unknown requester"],
        "requests": [_request_payload(request)],
        "raw_requests": [request],
        "managed_files": [],
        "scopes": [{
            "scope_type": "movie" if media_type == "movie" else "series",
            "season_number": None,
            "records": [_request_record(request)],
            "request_actionable": _request_is_actionable(request.get("status")),
            "latest_request_at": _request_payload(request).get("created_at"),
            "files": [],
            "forced_decision": decision,
            "forced_reason": reason,
        }],
    }


def _file_has_play(
    file_row: dict[str, Any], eligibility: datetime, history_index: dict[str, list[datetime]]
) -> bool:
    rating_key = str(file_row.get("plex_rating_key") or "")
    if rating_key and any(viewed >= eligibility for viewed in history_index.get(rating_key, [])):
        return True
    last_viewed = _parse_datetime(file_row.get("plex_last_viewed_at"), required=False)
    return bool(last_viewed and last_viewed >= eligibility)


def _finalize_review_items(
    review_items: list[dict[str, Any]],
    history_index: dict[str, list[datetime]],
    minimum_days: int,
    candidate_by_key: dict[str, dict[str, Any]],
) -> None:
    now = datetime.now(UTC)
    for item in review_items:
        candidate = candidate_by_key.get(str(item.get("candidate_key") or ""))
        item["deletion_eligible"] = bool(candidate and candidate.get("status") == "active")
        decisions: list[str] = []
        ready_files = 0
        for scope in item.get("scopes", []):
            counts = {"review_ready": 0, "waiting": 0, "protected": 0, "needs_attention": 0}
            planning_count = 0
            if scope.get("forced_decision"):
                decision = str(scope["forced_decision"])
                reason = str(scope.get("forced_reason") or "This scope could not be evaluated.")
            elif not scope.get("request_actionable"):
                decision = "not_actionable"
                reason = "The Seerr request is not approved or completed, so no retention action is available."
            elif not scope.get("files"):
                decision = "needs_attention"
                reason = "No managed files were found for this requested scope."
                counts["needs_attention"] = 1
            else:
                for file_row in scope["files"]:
                    eligibility = _parse_datetime(file_row.get("eligible_since"), required=False)
                    if (
                        not file_row.get("service_file_id")
                        or not file_row.get("path")
                        or int(file_row.get("size_bytes") or 0) <= 0
                        or eligibility is None
                    ):
                        file_row["review_decision"] = "needs_attention"
                        file_row["review_reason"] = "Required Arr file metadata or timestamps are missing."
                    elif now - eligibility < timedelta(days=minimum_days):
                        remaining = max(1, minimum_days - (now - eligibility).days)
                        file_row["review_decision"] = "waiting"
                        file_row["review_reason"] = f"The file has {remaining} days remaining in the waiting period."
                    elif file_row.get("match_status") != "matched":
                        file_row["review_decision"] = "needs_attention"
                        file_row["review_reason"] = "The Arr file could not be mapped exactly to one Plex item."
                    elif not file_row.get("media_atlas_file_id"):
                        file_row["review_decision"] = "needs_attention"
                        file_row["review_reason"] = "The mapped Plex file is not present in the Media Atlas inventory."
                    elif _file_has_play(file_row, eligibility, history_index):
                        file_row["review_decision"] = "protected"
                        file_row["review_reason"] = "A Plex play exists at or after this file's eligibility date."
                    else:
                        file_row["review_decision"] = "review_ready"
                        file_row["review_reason"] = "The requested file is old enough, mapped, and has no qualifying Plex play."
                    file_decision = str(file_row["review_decision"])
                    counts[file_decision] = counts.get(file_decision, 0) + 1
                    planner_eligible = bool(
                        file_decision == "review_ready"
                        and file_row.get("media_atlas_file_id")
                        and file_row.get("recommendation_category") in PLANNER_CATEGORIES
                    )
                    file_row["planning_eligible"] = planner_eligible
                    planning_count += int(planner_eligible)
                if counts["review_ready"]:
                    decision = "review_ready"
                elif counts["needs_attention"]:
                    decision = "needs_attention"
                elif counts["waiting"]:
                    decision = "waiting"
                elif counts["protected"]:
                    decision = "protected"
                else:
                    decision = "not_actionable"
                parts = []
                for key, label in (
                    ("review_ready", "ready"),
                    ("waiting", "waiting"),
                    ("protected", "protected by play evidence"),
                    ("needs_attention", "needing attention"),
                ):
                    if counts[key]:
                        parts.append(f"{counts[key]} {label}")
                reason = f"File review: {', '.join(parts)}."
            scope["decision"] = decision
            scope["reason"] = reason
            scope["review_ready_file_count"] = counts["review_ready"]
            scope["waiting_file_count"] = counts["waiting"]
            scope["protected_file_count"] = counts["protected"]
            scope["attention_file_count"] = counts["needs_attention"]
            scope["planning_eligible_file_count"] = planning_count
            scope["file_count"] = len(scope.get("files", []))
            scope["total_size_bytes"] = sum(int(file_row.get("size_bytes") or 0) for file_row in scope.get("files", []))
            decisions.append(decision)
            ready_files += counts["review_ready"]
        if "review_ready" in decisions:
            item["overall_decision"] = "review_ready"
        elif "needs_attention" in decisions:
            item["overall_decision"] = "needs_attention"
        elif "waiting" in decisions:
            item["overall_decision"] = "waiting"
        elif "protected" in decisions:
            item["overall_decision"] = "protected"
        else:
            item["overall_decision"] = "not_actionable"
        item["review_ready_file_count"] = ready_files
        item["total_file_count"] = len(item.get("managed_files", []))
        item["total_size_bytes"] = sum(int(file_row.get("size_bytes") or 0) for file_row in item.get("managed_files", []))
        scope_summary = ", ".join(
            f"{decisions.count(decision)} {decision.replace('_', ' ')}"
            for decision in ("review_ready", "waiting", "protected", "needs_attention", "not_actionable")
            if decisions.count(decision)
        )
        item["reason"] = f"Scope review: {scope_summary}."


def _build_subject(
    connection: dict[str, Any],
    item: dict[str, Any],
    files: list[dict[str, Any]],
    requests: list[dict[str, Any]],
    minimum_days: int,
) -> dict[str, Any] | None:
    managed = [_arr_file_payload(connection, item, file_row) for file_row in files]
    if (
        not managed
        or not requests
        or sum(file_row["size_bytes"] for file_row in managed) <= 0
        or any(not file_row["service_file_id"] or not file_row["path"] for file_row in managed)
    ):
        return None
    request_dates = [_parse_datetime(request.get("createdAt") or request.get("created_at")) for request in requests]
    file_dates = [_parse_datetime(file_row["date_added"]) for file_row in managed if file_row.get("date_added")]
    if len(file_dates) != len(managed):
        return None
    latest_request = max(request_dates)
    available_since = max(file_dates)
    eligible_since = max(latest_request, available_since)
    if datetime.now(UTC) - eligible_since < timedelta(days=minimum_days):
        return None
    requesters = sorted({name for name in (_requester_name(item) for item in requests) if name}, key=str.casefold)
    if not requesters:
        requesters = ["Unknown requester"]
    media = requests[0].get("media") or {}
    return {
        "connection_id": connection["id"],
        "service_item_id": int(item["id"]),
        "seerr_media_id": _as_int(media.get("id")),
        "media_type": "movie" if connection["service_type"] == "radarr" else "tv",
        "title": str(item.get("title") or item.get("sortTitle") or "Untitled"),
        "year": _as_int(item.get("year")),
        "tmdb_id": _as_int(item.get("tmdbId") or media.get("tmdbId")),
        "tvdb_id": _as_int(item.get("tvdbId") or media.get("tvdbId")),
        "is_4k": bool(any(request.get("is4k") or request.get("is_4k") for request in requests)),
        "size_bytes": sum(int(file_row["size_bytes"]) for file_row in managed),
        "file_count": len(managed),
        "matched_file_count": 0,
        "requesters": requesters,
        "requests": [
            {
                "id": _as_int(request.get("id")),
                "created_at": _iso(_parse_datetime(request.get("createdAt") or request.get("created_at"))),
                "requester": _requester_name(request) or "Unknown requester",
                "is_4k": bool(request.get("is4k") or request.get("is_4k")),
            }
            for request in requests
        ],
        "latest_request_at": _iso(latest_request),
        "available_since": _iso(available_since),
        "eligible_since": _iso(eligible_since),
        "files": managed,
    }


def _arr_file_payload(
    connection: dict[str, Any], item: dict[str, Any], file_row: dict[str, Any]
) -> dict[str, Any]:
    path = str(file_row.get("path") or "")
    if not path:
        base = str(item.get("path") or "").rstrip("/\\")
        relative = str(file_row.get("relativePath") or file_row.get("relative_path") or "").lstrip("/\\")
        path = f"{base}/{relative}" if relative else base
    normalized = _apply_arr_mappings(path, connection.get("path_mappings") or [])
    return {
        "service_file_id": int(_as_int(file_row.get("id")) or 0),
        "season_number": _season_number(file_row),
        "path": path,
        "normalized_path": normalized,
        "size_bytes": int(_as_int(file_row.get("size")) or 0),
        "date_added": _iso(_parse_datetime(file_row.get("dateAdded") or file_row.get("date_added")))
        if file_row.get("dateAdded") or file_row.get("date_added") else None,
        "media_atlas_file_id": None,
        "plex_item_id": None,
        "plex_rating_key": None,
        "plex_last_viewed_at": None,
        "match_status": "unmatched",
        "recommendation_category": None,
    }


def _map_file(file_row: dict[str, Any]) -> None:
    matches = db.query_all(
        """
        SELECT pmp.plex_item_id, pi.rating_key, pi.last_viewed_at
        FROM plex_media_parts pmp
        JOIN plex_items pi ON pi.id = pmp.plex_item_id
        WHERE pmp.normalized_path = ? AND pi.is_stale = 0
        ORDER BY pmp.id
        """,
        (file_row["normalized_path"],),
    )
    inventory = db.query_all(
        "SELECT id, recommendation_category FROM files WHERE path = ? AND is_missing = 0 ORDER BY id",
        (file_row["normalized_path"],),
    )
    if not inventory:
        inventory = db.query_all(
            "SELECT id, recommendation_category FROM files WHERE REPLACE(path, '\\', '/') = ? AND is_missing = 0 ORDER BY id",
            (file_row["normalized_path"],),
        )
    file_row["media_atlas_file_id"] = int(inventory[0]["id"]) if len(inventory) == 1 else None
    file_row["recommendation_category"] = inventory[0].get("recommendation_category") if len(inventory) == 1 else None
    if len(matches) == 1:
        file_row["plex_item_id"] = int(matches[0]["plex_item_id"])
        file_row["plex_rating_key"] = str(matches[0]["rating_key"])
        file_row["plex_last_viewed_at"] = matches[0].get("last_viewed_at")
        file_row["match_status"] = "matched"
    elif len(matches) > 1:
        file_row["match_status"] = "ambiguous"


def _map_analysis_files(
    review_items: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> None:
    for review_item in review_items:
        for file_row in review_item.get("managed_files", []):
            _map_file(file_row)
    for candidate in candidates:
        for file_row in candidate["files"]:
            _map_file(file_row)
        candidate["matched_file_count"] = sum(
            1 for item in candidate["files"] if item["match_status"] == "matched"
        )


def _history_index(history: list[dict[str, Any]]) -> dict[str, list[datetime]]:
    result: dict[str, list[datetime]] = defaultdict(list)
    for event in history:
        key = str(event.get("ratingKey") or event.get("rating_key") or "")
        viewed = _history_datetime(event)
        if key and viewed:
            result[key].append(viewed)
    return result


def _subject_has_play(
    subject: dict[str, Any], eligibility: datetime, history_index: dict[str, list[datetime]]
) -> bool:
    for file_row in subject["files"]:
        rating_key = str(file_row.get("plex_rating_key") or "")
        if rating_key and any(viewed >= eligibility for viewed in history_index.get(rating_key, [])):
            return True
        last_viewed = file_row.get("plex_last_viewed_at")
        if last_viewed and _parse_datetime(last_viewed) >= eligibility:
            return True
    return False


def _watch_event_row(job_id: int, event: dict[str, Any]) -> tuple[Any, ...] | None:
    rating_key = str(event.get("ratingKey") or event.get("rating_key") or "")
    viewed = _history_datetime(event)
    if not rating_key or not viewed:
        return None
    account = event.get("Account") or event.get("account") or {}
    if isinstance(account, list):
        account = account[0] if account else {}
    account_id = _as_int(event.get("accountID") or event.get("accountId") or account.get("id"))
    history_key = str(event.get("historyKey") or event.get("key") or "") or None
    return (
        job_id,
        history_key,
        rating_key,
        account_id,
        _iso(viewed),
        event.get("type"),
        event.get("title") or event.get("grandparentTitle"),
        db.dumps(event),
    )


def _apply_history_evidence(
    job_id: int,
    history: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    review_items: list[dict[str, Any]],
    minimum_unwatched_days: int,
) -> list[tuple[Any, ...]]:
    history_index = _history_index(history)
    watch_rows = [
        event_row
        for event in history
        if (event_row := _watch_event_row(job_id, event)) is not None
    ]
    now = datetime.now(UTC)
    for candidate in candidates:
        eligibility = _parse_datetime(candidate["eligible_since"])
        protected = _subject_has_play(candidate, eligibility, history_index)
        coverage_complete = candidate["matched_file_count"] == candidate["file_count"]
        age = max(0, (now - eligibility).days)
        names = ", ".join(candidate["requesters"])
        if candidate.get("deletion_gate_passed") and not protected and coverage_complete:
            candidate["status"] = "active"
            candidate["reason"] = (
                f"Requested by {names}; eligible for {age} days; no Plex account has played "
                "the mapped copy since its eligibility date."
            )
        elif candidate.get("deletion_gate_passed") and not protected:
            missing = candidate["file_count"] - candidate["matched_file_count"]
            candidate["status"] = "diagnostic"
            candidate["reason"] = (
                f"No qualifying Plex plays were found, but {missing} of {candidate['file_count']} "
                "managed files could not be mapped exactly. Deletion is disabled."
            )
        else:
            candidate["status"] = "review_only"
            candidate["reason"] = (
                "Whole-copy deletion safeguards are not satisfied; eligible movie or season files "
                "remain available through retention review."
            )
    candidate_by_key = {item["candidate_key"]: item for item in candidates}
    _finalize_review_items(
        review_items,
        history_index,
        minimum_unwatched_days,
        candidate_by_key,
    )
    return watch_rows


def _publish_snapshot(
    job_id: int,
    candidates: list[dict[str, Any]],
    review_items: list[dict[str, Any]],
    watch_rows: list[tuple[Any, ...]],
    warnings: list[dict[str, Any]],
) -> None:
    candidate_count = sum(1 for item in candidates if item["status"] == "active")
    diagnostic_count = sum(1 for item in candidates if item["status"] == "diagnostic")
    total_size = sum(item["size_bytes"] for item in candidates if item["status"] == "active")
    scopes = [scope for item in review_items for scope in item.get("scopes", [])]
    review_ready_scope_count = sum(1 for scope in scopes if scope.get("decision") == "review_ready")
    waiting_scope_count = sum(1 for scope in scopes if scope.get("decision") == "waiting")
    protected_scope_count = sum(1 for scope in scopes if scope.get("decision") == "protected")
    attention_scope_count = sum(1 for scope in scopes if scope.get("decision") == "needs_attention")
    review_ready_size = sum(
        int(file_row.get("size_bytes") or 0)
        for scope in scopes
        for file_row in scope.get("files", [])
        if file_row.get("review_decision") == "review_ready"
    )
    with db.connect() as connection:
        connection.execute("DELETE FROM retention_review_items WHERE analysis_job_id = ?", (job_id,))
        connection.execute("DELETE FROM retention_candidates WHERE analysis_job_id = ?", (job_id,))
        connection.execute("DELETE FROM plex_watch_events WHERE analysis_job_id = ?", (job_id,))
        for event_row in watch_rows:
            connection.execute(
                """
                INSERT OR IGNORE INTO plex_watch_events (
                    analysis_job_id, history_key, rating_key, account_id, viewed_at, media_type, title, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                event_row,
            )
        candidate_ids: dict[str, int] = {}
        candidate_file_ids: dict[tuple[str, int], int] = {}
        for item in candidates:
            cursor = connection.execute(
                """
                INSERT INTO retention_candidates (
                    analysis_job_id, connection_id, service_item_id, seerr_media_id, media_type,
                    title, year, tmdb_id, tvdb_id, is_4k, size_bytes, file_count,
                    matched_file_count, requesters_json, requests_json, latest_request_at,
                    available_since, eligible_since, reason, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id, item["connection_id"], item["service_item_id"], item.get("seerr_media_id"),
                    item["media_type"], item["title"], item.get("year"), item.get("tmdb_id"),
                    item.get("tvdb_id"), 1 if item.get("is_4k") else 0, item["size_bytes"],
                    item["file_count"], item["matched_file_count"], db.dumps(item["requesters"]),
                    db.dumps(item["requests"]), item["latest_request_at"], item["available_since"],
                    item["eligible_since"], item["reason"], item["status"], db.utc_now(),
                ),
            )
            candidate_id = int(cursor.lastrowid)
            candidate_key = str(item["candidate_key"])
            candidate_ids[candidate_key] = candidate_id
            for file_row in item["files"]:
                file_cursor = connection.execute(
                    """
                    INSERT INTO retention_candidate_files (
                        candidate_id, service_file_id, path, normalized_path, size_bytes, date_added,
                        media_atlas_file_id, plex_item_id, plex_rating_key, match_status, season_number
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        candidate_id, file_row["service_file_id"], file_row["path"],
                        file_row["normalized_path"], file_row["size_bytes"], file_row.get("date_added"),
                        file_row.get("media_atlas_file_id"), file_row.get("plex_item_id"),
                        file_row.get("plex_rating_key"), file_row["match_status"],
                        file_row.get("season_number"),
                    ),
                )
                candidate_file_ids[(candidate_key, int(file_row["service_file_id"]))] = int(file_cursor.lastrowid)
        for item in review_items:
            candidate_key = str(item.get("candidate_key") or "")
            review_cursor = connection.execute(
                """
                INSERT INTO retention_review_items (
                    analysis_job_id, candidate_id, connection_id, result_key, service_item_id,
                    seerr_media_id, media_type, title, year, tmdb_id, tvdb_id, is_4k,
                    requesters_json, requests_json, overall_decision, reason, deletion_eligible,
                    total_size_bytes, total_file_count, review_ready_file_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    candidate_ids.get(candidate_key),
                    item.get("connection_id"),
                    item["result_key"],
                    item.get("service_item_id"),
                    item.get("seerr_media_id"),
                    item["media_type"],
                    item["title"],
                    item.get("year"),
                    item.get("tmdb_id"),
                    item.get("tvdb_id"),
                    1 if item.get("is_4k") else 0,
                    db.dumps(item.get("requesters") or []),
                    db.dumps(item.get("requests") or []),
                    item["overall_decision"],
                    item["reason"],
                    1 if item.get("deletion_eligible") else 0,
                    int(item.get("total_size_bytes") or 0),
                    int(item.get("total_file_count") or 0),
                    int(item.get("review_ready_file_count") or 0),
                    db.utc_now(),
                ),
            )
            review_item_id = int(review_cursor.lastrowid)
            for scope in item.get("scopes", []):
                scope_cursor = connection.execute(
                    """
                    INSERT INTO retention_review_scopes (
                        review_item_id, scope_type, season_number, decision, reason,
                        latest_request_at, total_size_bytes, file_count,
                        review_ready_file_count, waiting_file_count, protected_file_count,
                        attention_file_count, planning_eligible_file_count, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        review_item_id,
                        scope["scope_type"],
                        scope.get("season_number"),
                        scope["decision"],
                        scope["reason"],
                        scope.get("latest_request_at"),
                        int(scope.get("total_size_bytes") or 0),
                        int(scope.get("file_count") or 0),
                        int(scope.get("review_ready_file_count") or 0),
                        int(scope.get("waiting_file_count") or 0),
                        int(scope.get("protected_file_count") or 0),
                        int(scope.get("attention_file_count") or 0),
                        int(scope.get("planning_eligible_file_count") or 0),
                        db.utc_now(),
                    ),
                )
                scope_id = int(scope_cursor.lastrowid)
                for file_row in scope.get("files", []):
                    service_file_id = int(file_row.get("service_file_id") or 0)
                    connection.execute(
                        """
                        INSERT INTO retention_review_files (
                            review_scope_id, candidate_file_id, service_file_id, path,
                            normalized_path, size_bytes, date_added, eligible_since,
                            media_atlas_file_id, plex_item_id, plex_rating_key, match_status,
                            decision, reason, planning_eligible, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            scope_id,
                            candidate_file_ids.get((candidate_key, service_file_id)),
                            service_file_id or None,
                            file_row.get("path") or "",
                            file_row.get("normalized_path") or "",
                            int(file_row.get("size_bytes") or 0),
                            file_row.get("date_added"),
                            file_row.get("eligible_since"),
                            file_row.get("media_atlas_file_id"),
                            file_row.get("plex_item_id"),
                            file_row.get("plex_rating_key"),
                            file_row.get("match_status") or "unmatched",
                            file_row.get("review_decision") or scope["decision"],
                            file_row.get("review_reason") or scope["reason"],
                            1 if file_row.get("planning_eligible") else 0,
                            db.utc_now(),
                        ),
                    )
        connection.execute(
            """
            UPDATE retention_analysis_jobs
            SET candidate_count = ?, diagnostic_count = ?, total_size_bytes = ?, warnings_json = ?,
                evaluated_title_count = ?, review_ready_scope_count = ?, waiting_scope_count = ?,
                protected_scope_count = ?, attention_scope_count = ?, review_ready_size_bytes = ?
            WHERE id = ?
            """,
            (
                candidate_count,
                diagnostic_count,
                total_size,
                db.dumps(warnings),
                len(review_items),
                review_ready_scope_count,
                waiting_scope_count,
                protected_scope_count,
                attention_scope_count,
                review_ready_size,
                job_id,
            ),
        )


def _inflate_connection(row: dict[str, Any], include_secret: bool) -> dict[str, Any]:
    result = dict(row)
    result["enabled"] = bool(result.get("enabled"))
    result["path_mappings"] = db.loads_json(result.pop("path_mappings_json", None), [])
    api_key = str(result.get("api_key") or "")
    if include_secret:
        return result
    result.pop("api_key", None)
    result["api_key_configured"] = bool(api_key)
    result["api_key_hint"] = f"...{api_key[-4:]}" if api_key else ""
    return result


def _inflate_job(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["warnings"] = db.loads_json(result.pop("warnings_json", None), [])
    result["cancel_requested"] = bool(result.get("cancel_requested"))
    return result


def _inflate_candidate(row: dict[str, Any], include_files: bool) -> dict[str, Any]:
    result = dict(row)
    result["is_4k"] = bool(result.get("is_4k"))
    result["requesters"] = db.loads_json(result.pop("requesters_json", None), [])
    result["requests"] = db.loads_json(result.pop("requests_json", None), [])
    result["mapping_coverage_percent"] = round(
        100 * int(result["matched_file_count"]) / max(1, int(result["file_count"])), 1
    )
    result["available_actions"] = []
    if not result.get("action_state"):
        if result["status"] != "review_only":
            result["available_actions"].append("transcode_plan")
        if result["status"] == "active":
            result["available_actions"].append("delete")
    if include_files:
        result["files"] = db.query_all(
            """
            SELECT rcf.*, f.filename, f.recommendation_category
            FROM retention_candidate_files rcf
            LEFT JOIN files f ON f.id = rcf.media_atlas_file_id
            WHERE rcf.candidate_id = ? ORDER BY rcf.path
            """,
            (result["id"],),
        )
        result["actions"] = list_actions(100, int(result["id"]))
    return result


def _inflate_review_item(row: dict[str, Any], include_files: bool) -> dict[str, Any]:
    result = dict(row)
    result["is_4k"] = bool(result.get("is_4k"))
    result["deletion_eligible"] = bool(result.get("deletion_eligible"))
    result["requesters"] = db.loads_json(result.pop("requesters_json", None), [])
    result["requests"] = db.loads_json(result.pop("requests_json", None), [])
    scopes = db.query_all(
        """
        SELECT * FROM retention_review_scopes
        WHERE review_item_id = ?
        ORDER BY CASE scope_type WHEN 'movie' THEN 0 WHEN 'season' THEN 1 ELSE 2 END,
                 season_number, id
        """,
        (result["id"],),
    )
    for scope in scopes:
        scope["available_actions"] = []
        if (
            scope.get("decision") == "review_ready"
            and int(scope.get("planning_eligible_file_count") or 0) > 0
            and result.get("candidate_id")
        ):
            scope["available_actions"].append("transcode_plan")
        if include_files:
            files = db.query_all(
                """
                SELECT rrf.*, f.filename, f.recommendation_category
                FROM retention_review_files rrf
                LEFT JOIN files f ON f.id = rrf.media_atlas_file_id
                WHERE rrf.review_scope_id = ?
                ORDER BY rrf.path, rrf.id
                """,
                (scope["id"],),
            )
            for file_row in files:
                file_row["planning_eligible"] = bool(file_row.get("planning_eligible"))
            scope["files"] = files
    result["scopes"] = scopes
    result["available_actions"] = []
    if any("transcode_plan" in scope["available_actions"] for scope in scopes):
        result["available_actions"].append("transcode_plan")
    if result["deletion_eligible"] and result.get("candidate_id"):
        result["available_actions"].append("delete")
    return result


def _create_action(
    candidate: dict[str, Any],
    action_type: str,
    requested_by: str | None,
    status: str,
    context: dict[str, Any] | None = None,
) -> int:
    return db.execute(
        """
        INSERT INTO retention_actions (
            candidate_id, action_type, status, requested_by, created_at, started_at, snapshot_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            candidate["id"], action_type, status, requested_by, db.utc_now(), db.utc_now(),
            db.dumps({
                "candidate_id": candidate["id"],
                "analysis_job_id": candidate["analysis_job_id"],
                "connection_id": candidate["connection_id"],
                "service_item_id": candidate["service_item_id"],
                "title": candidate["title"],
                "size_bytes": candidate["size_bytes"],
                "files": candidate.get("files") or [],
                **(context or {}),
            }),
        ),
    )


def _finish_action(
    action_id: int, status: str, result: dict[str, Any] | None, error: str | None = None
) -> None:
    db.execute(
        """
        UPDATE retention_actions SET status = ?, finished_at = ?, result_json = ?, error_message = ?
        WHERE id = ?
        """,
        (status, db.utc_now(), db.dumps(result) if result is not None else None, error, action_id),
    )


def _update_job(
    job_id: int,
    *,
    status: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    current_stage: str | None = None,
    progress: float | None = None,
    message: str | None = None,
    error: str | None = None,
    warnings: list[dict[str, Any]] | None = None,
) -> None:
    updates: list[str] = []
    params: list[Any] = []
    values = {
        "status": status,
        "started_at": started_at,
        "finished_at": finished_at,
        "current_stage": current_stage,
        "progress_percent": progress,
        "message": message,
        "error_message": error,
        "warnings_json": db.dumps(warnings) if warnings is not None else None,
    }
    for column, value in values.items():
        if value is not None:
            updates.append(f"{column} = ?")
            params.append(value)
    if updates:
        params.append(job_id)
        db.execute(f"UPDATE retention_analysis_jobs SET {', '.join(updates)} WHERE id = ?", tuple(params))


class _AnalysisCanceled(Exception):
    pass


def _check_canceled(job_id: int) -> None:
    row = db.query_one("SELECT cancel_requested FROM retention_analysis_jobs WHERE id = ?", (job_id,))
    if row and row.get("cancel_requested"):
        raise _AnalysisCanceled()


def _scheduled_run_exists(local_date: str) -> bool:
    rows = db.query_all(
        "SELECT created_at FROM retention_analysis_jobs WHERE trigger_type = 'scheduled' ORDER BY id DESC LIMIT 2"
    )
    for row in rows:
        try:
            created = datetime.fromisoformat(str(row["created_at"])).astimezone()
        except ValueError:
            continue
        if created.date().isoformat() == local_date:
            return True
    return False


def _requester_name(request: dict[str, Any]) -> str:
    user = request.get("requestedBy") or request.get("user") or request.get("requester") or {}
    if isinstance(user, str):
        return user.strip()
    return str(
        user.get("displayName") or user.get("display_name") or user.get("username") or user.get("email") or ""
    ).strip()


def _apply_arr_mappings(path: str, mappings: list[dict[str, str]]) -> str:
    value = normalize_path(path)
    ordered = sorted(mappings, key=lambda item: len(item["source_path_prefix"]), reverse=True)
    for mapping in ordered:
        source = mapping["source_path_prefix"]
        if value == source or value.startswith(f"{source}/"):
            return normalize_path(f"{mapping['media_atlas_path_prefix']}{value[len(source):]}")
    return value


def _clean_path_mappings(value: Any) -> list[dict[str, str]]:
    mappings = []
    for item in _as_list(value):
        if not isinstance(item, dict):
            continue
        source = normalize_path(item.get("source_path_prefix") or item.get("source") or "")
        target = normalize_path(item.get("media_atlas_path_prefix") or item.get("target") or "")
        if source and target:
            mappings.append({"source_path_prefix": source, "media_atlas_path_prefix": target})
    return mappings


def _env_path_mappings(value: str) -> list[dict[str, str]]:
    pairs = []
    for item in str(value or "").split(";"):
        if "=" not in item:
            continue
        source, target = item.split("=", 1)
        pairs.append({"source_path_prefix": source.strip(), "media_atlas_path_prefix": target.strip()})
    return _clean_path_mappings(pairs)


def _history_datetime(event: dict[str, Any]) -> datetime | None:
    return _parse_datetime(event.get("viewedAt") or event.get("viewed_at"), required=False)


def _parse_datetime(value: Any, required: bool = True) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        result = datetime.fromtimestamp(int(value), UTC)
    elif value:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            result = datetime.fromisoformat(text)
        except ValueError:
            if required:
                raise MediaRetentionError(f"Invalid source timestamp: {value}")
            return None
    else:
        if required:
            raise MediaRetentionError("A required source timestamp is missing.")
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)
    return result.astimezone(UTC)


def _iso(value: datetime | None) -> str:
    if value is None:
        raise MediaRetentionError("A required timestamp is missing.")
    return value.astimezone(UTC).isoformat(timespec="seconds")


def _clean_schedule_time(value: Any) -> str:
    text = str(value or "03:00")
    try:
        parsed = datetime.strptime(text, "%H:%M")
    except ValueError as exc:
        raise MediaRetentionError("Schedule time must use 24-hour HH:MM format.") from exc
    return parsed.strftime("%H:%M")


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_timeout(exc: Exception) -> bool:
    try:
        import httpx
        return isinstance(exc, httpx.TimeoutException)
    except ModuleNotFoundError:
        return exc.__class__.__name__.lower().endswith("timeoutexception")
