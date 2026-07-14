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
    if db.query_one("SELECT id FROM retention_candidates WHERE connection_id = ? LIMIT 1", (connection_id,)):
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
            subjects: list[dict[str, Any]] = []
            completed_connections = 0
            type_counts = {
                service_type: sum(1 for item in arr_connections if item["service_type"] == service_type)
                for service_type in ("sonarr", "radarr")
            }
            for connection in arr_connections:
                client = self.client_factory(connection, settings["timeout_seconds"])
                try:
                    catalog = await client.arr_catalog()
                    connection["service_type_connection_count"] = type_counts[connection["service_type"]]
                    matching_requests = _requests_for_connection(requests, connection)
                    grouped = _match_requests_to_catalog(matching_requests, catalog, connection)
                    connection_subjects: list[dict[str, Any]] = []
                    for item, item_requests in grouped:
                        _check_canceled(job_id)
                        files = await client.arr_files(int(item["id"]))
                        subject = _build_subject(
                            connection,
                            item,
                            files,
                            item_requests,
                            int(settings["minimum_unwatched_days"]),
                        )
                        if subject:
                            connection_subjects.append(subject)
                    subjects.extend(connection_subjects)
                    completed_connections += 1
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
                progress = 22 + int(38 * completed_connections / max(1, len(arr_connections)))
                _update_job(job_id, current_stage="arr", progress=progress,
                            message=f"Read {completed_connections} of {len(arr_connections)} Arr instances.",
                            warnings=warnings)
            _check_canceled(job_id)

            _update_job(job_id, current_stage="mapping", progress=65, message="Matching managed files to Plex parts.")
            for subject in subjects:
                for file_row in subject["files"]:
                    _map_file(file_row)
                subject["matched_file_count"] = sum(
                    1 for item in subject["files"] if item["match_status"] == "matched"
                )
            _check_canceled(job_id)

            earliest = min((_parse_datetime(item["eligible_since"]) for item in subjects), default=None)
            history: list[dict[str, Any]] = []
            if earliest:
                _update_job(job_id, current_stage="history", progress=76, message="Reading Plex play history.")
                history = await PlexClient(plex_settings).history(earliest)
            history_index = _history_index(history)
            watch_rows: list[tuple[Any, ...]] = []
            for event in history:
                event_row = _watch_event_row(job_id, event)
                if event_row:
                    watch_rows.append(event_row)

            published: list[dict[str, Any]] = []
            now = datetime.now(UTC)
            for subject in subjects:
                eligibility = _parse_datetime(subject["eligible_since"])
                protected = _subject_has_play(subject, eligibility, history_index)
                if protected:
                    continue
                coverage_complete = subject["matched_file_count"] == subject["file_count"]
                age = max(0, (now - eligibility).days)
                names = ", ".join(subject["requesters"])
                if coverage_complete:
                    subject["status"] = "active"
                    subject["reason"] = (
                        f"Requested by {names}; eligible for {age} days; no Plex account has played "
                        "the mapped copy since its eligibility date."
                    )
                else:
                    missing = subject["file_count"] - subject["matched_file_count"]
                    subject["status"] = "diagnostic"
                    subject["reason"] = (
                        f"No qualifying Plex plays were found, but {missing} of {subject['file_count']} "
                        "managed files could not be mapped exactly. Deletion is disabled."
                    )
                published.append(subject)
            _check_canceled(job_id)

            _update_job(job_id, current_stage="publish", progress=92, message="Publishing candidate snapshot.")
            _publish_snapshot(job_id, published, watch_rows, warnings)
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
    counts = {"candidate_count": 0, "diagnostic_count": 0, "total_size_bytes": 0}
    if snapshot_id:
        counts = db.query_one(
            """
            SELECT
              SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS candidate_count,
              SUM(CASE WHEN status = 'diagnostic' THEN 1 ELSE 0 END) AS diagnostic_count,
              COALESCE(SUM(CASE WHEN status = 'active' THEN size_bytes ELSE 0 END), 0) AS total_size_bytes
            FROM retention_candidates WHERE analysis_job_id = ?
            """,
            (snapshot_id,),
        ) or counts
    configured = list_connections()
    return {
        "candidate_count": int(counts.get("candidate_count") or 0),
        "diagnostic_count": int(counts.get("diagnostic_count") or 0),
        "total_size_bytes": int(counts.get("total_size_bytes") or 0),
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
) -> dict[str, Any]:
    candidate = read_candidate(candidate_id)
    if not candidate:
        raise MediaRetentionError("Retention candidate not found.")
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
    action_id = _create_action(candidate, "transcode_plan", requested_by, "running")
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


def _requests_for_connection(requests: list[dict[str, Any]], connection: dict[str, Any]) -> list[dict[str, Any]]:
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
        if request.get("status") in DECLINED_STATUSES:
            continue
        is_4k = bool(request.get("is4k") or request.get("is_4k"))
        media_status = media.get("status4k") if is_4k else media.get("status")
        if media_status not in AVAILABLE_STATUSES:
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
        "SELECT id FROM files WHERE path = ? AND is_missing = 0 ORDER BY id",
        (file_row["normalized_path"],),
    )
    if not inventory:
        inventory = db.query_all(
            "SELECT id FROM files WHERE REPLACE(path, '\\', '/') = ? AND is_missing = 0 ORDER BY id",
            (file_row["normalized_path"],),
        )
    file_row["media_atlas_file_id"] = int(inventory[0]["id"]) if len(inventory) == 1 else None
    if len(matches) == 1:
        file_row["plex_item_id"] = int(matches[0]["plex_item_id"])
        file_row["plex_rating_key"] = str(matches[0]["rating_key"])
        file_row["plex_last_viewed_at"] = matches[0].get("last_viewed_at")
        file_row["match_status"] = "matched"
    elif len(matches) > 1:
        file_row["match_status"] = "ambiguous"


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


def _publish_snapshot(
    job_id: int,
    candidates: list[dict[str, Any]],
    watch_rows: list[tuple[Any, ...]],
    warnings: list[dict[str, Any]],
) -> None:
    candidate_count = sum(1 for item in candidates if item["status"] == "active")
    diagnostic_count = sum(1 for item in candidates if item["status"] == "diagnostic")
    total_size = sum(item["size_bytes"] for item in candidates if item["status"] == "active")
    with db.connect() as connection:
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
            for file_row in item["files"]:
                connection.execute(
                    """
                    INSERT INTO retention_candidate_files (
                        candidate_id, service_file_id, path, normalized_path, size_bytes, date_added,
                        media_atlas_file_id, plex_item_id, plex_rating_key, match_status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        candidate_id, file_row["service_file_id"], file_row["path"],
                        file_row["normalized_path"], file_row["size_bytes"], file_row.get("date_added"),
                        file_row.get("media_atlas_file_id"), file_row.get("plex_item_id"),
                        file_row.get("plex_rating_key"), file_row["match_status"],
                    ),
                )
        connection.execute(
            """
            UPDATE retention_analysis_jobs
            SET candidate_count = ?, diagnostic_count = ?, total_size_bytes = ?, warnings_json = ?
            WHERE id = ?
            """,
            (candidate_count, diagnostic_count, total_size, db.dumps(warnings), job_id),
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


def _create_action(
    candidate: dict[str, Any], action_type: str, requested_by: str | None, status: str
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
