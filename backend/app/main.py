from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db
from .config import CONFIG, DEFAULT_EXCLUDES, DEFAULT_EXTENSIONS
from .health import admin_status, diagnostics_status, live_status, metrics_status, readiness_status
from .logging_config import configure_logging
from .security import (
    authenticated_user,
    auth_status,
    clear_session_cookie,
    login,
    require_auth_response,
    require_csrf_response,
    security_headers,
    set_session_cookie,
)
from .services.paths import is_within_allowed_browse_roots, resolve_existing_directory
from .services.plex import (
    PlexError,
    PlexSyncManager,
    get_settings as get_plex_settings,
    inflate_plex,
    list_sync_jobs as list_plex_sync_jobs,
    plex_join_clause,
    plex_select_columns,
    read_sync_job as read_plex_sync_job,
    save_settings as save_plex_settings,
    status_summary as plex_status_summary,
    stored_libraries as stored_plex_libraries,
    unmatched as plex_unmatched,
)
from .services.media_retention import (
    AmbiguousDeleteError,
    MediaRetentionError,
    MediaRetentionManager,
    candidate_export_rows,
    create_candidate_transcode_plan,
    create_connection as create_retention_connection,
    delete_candidate as delete_retention_candidate,
    delete_connection as delete_retention_connection,
    get_settings as get_media_retention_settings,
    list_actions as list_retention_actions,
    list_analysis_jobs as list_retention_analysis_jobs,
    list_candidates as list_retention_candidates,
    list_connections as list_retention_connections,
    read_analysis_job as read_retention_analysis_job,
    read_candidate as read_retention_candidate,
    retention_summary,
    retry_seerr_reconciliation,
    save_settings as save_media_retention_settings,
    update_connection as update_retention_connection,
)
from .services.retention import apply_retention
from .services.scanner import ScanManager
from .services.transcodes import TranscodeManager, create_plan, get_plan, transcode_savings_stats

configure_logging()
logger = logging.getLogger("media_atlas.requests")

app = FastAPI(title="Media Atlas", version=CONFIG.version.version)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CONFIG.operations.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

scan_manager = ScanManager()
transcode_manager = TranscodeManager()
plex_manager = PlexSyncManager()
media_retention_manager = MediaRetentionManager(plex_manager)


class RootCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    path: str = Field(min_length=1)
    enabled: bool = True
    include_extensions: list[str] = Field(default_factory=lambda: DEFAULT_EXTENSIONS)
    exclude_patterns: list[str] = Field(default_factory=lambda: DEFAULT_EXCLUDES)


class RootUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    path: str | None = Field(default=None, min_length=1)
    enabled: bool | None = None
    include_extensions: list[str] | None = None
    exclude_patterns: list[str] | None = None


class PlanCreate(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    profile_id: int
    file_ids: list[int] = Field(min_length=1)
    notes: str | None = None


class RunCreate(BaseModel):
    plan_id: int
    name: str | None = Field(default=None, max_length=180)


class PublishRunItemRequest(BaseModel):
    source_path: str = Field(min_length=1)
    target_path: str = Field(min_length=1)
    confirmation_text: str = Field(min_length=1)


class CleanupRunRequest(BaseModel):
    confirmation_text: str = Field(min_length=1)
    archive_run: bool = True


class CleanupRunItemRequest(BaseModel):
    confirmation_text: str = Field(min_length=1)


class ValidateRunItemRequest(BaseModel):
    confirmation_text: str = Field(min_length=1)
    message: str | None = Field(default=None, max_length=500)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class PlexPathMapping(BaseModel):
    plex_path_prefix: str = ""
    media_atlas_path_prefix: str = ""


class PlexSettingsUpdate(BaseModel):
    enabled: bool | None = None
    server_url: str | None = None
    token: str | None = None
    clear_token: bool = False
    selected_library_keys: list[str] | None = None
    timeout_seconds: int | None = Field(default=None, ge=1, le=120)
    path_mappings: list[PlexPathMapping] | None = None


class RetentionPathMapping(BaseModel):
    source_path_prefix: str = Field(min_length=1)
    media_atlas_path_prefix: str = Field(min_length=1)


class RetentionConnectionCreate(BaseModel):
    service_type: Literal["seerr", "sonarr", "radarr"]
    name: str = Field(min_length=1, max_length=120)
    server_url: str = Field(min_length=1)
    api_key: str = Field(min_length=1)
    enabled: bool = True
    seerr_service_id: int | None = None
    path_mappings: list[RetentionPathMapping] = Field(default_factory=list)


class RetentionConnectionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    server_url: str | None = Field(default=None, min_length=1)
    api_key: str | None = None
    clear_api_key: bool = False
    enabled: bool | None = None
    seerr_service_id: int | None = None
    path_mappings: list[RetentionPathMapping] | None = None


class RetentionSettingsUpdate(BaseModel):
    minimum_unwatched_days: int | None = Field(default=None, ge=1, le=3650)
    schedule_enabled: bool | None = None
    schedule_time: str | None = None
    timeout_seconds: int | None = Field(default=None, ge=1, le=120)


class RetentionTranscodePlanCreate(BaseModel):
    profile_id: int
    file_ids: list[int] | None = None
    name: str | None = Field(default=None, max_length=180)


class RetentionDeleteRequest(BaseModel):
    confirmation_text: str = Field(min_length=1)


@app.middleware("http")
async def request_security_middleware(request: Request, call_next: Any) -> Response:
    request_id = request.headers.get("X-Request-ID") or secrets.token_hex(8)
    started = time.perf_counter()
    response: Response | None = require_auth_response(request)
    if response is None:
        response = require_csrf_response(request)
    if response is None:
        response = await call_next(request)
    security_headers(response)
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "request complete",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        },
    )
    return response


@app.on_event("startup")
async def startup() -> None:
    db.init_db()
    for warning in CONFIG.config_warnings:
        logging.getLogger("media_atlas.config").warning(warning)
    apply_retention()
    await scan_manager.recover_startup_jobs()
    await plex_manager.recover_startup_jobs()
    await media_retention_manager.recover_startup_jobs()
    await transcode_manager.recover_startup_jobs()


@app.get("/api/health")
async def health() -> dict[str, Any]:
    status = readiness_status()
    return {
        "status": status["status"],
        "database_available": status["database"]["ok"],
        "ffprobe_available": status["tools"]["ffprobe"]["available"],
        "ffmpeg_available": status["tools"]["ffmpeg"]["available"],
        "data_dir": str(CONFIG.data_dir),
        "reports_dir": str(CONFIG.reports_dir),
        "logs_dir": str(CONFIG.logs_dir),
        "transcode_staging_dir": str(CONFIG.transcoder.staging_dir),
        "transcode_backup_dir": str(CONFIG.transcoder.backup_dir),
        "readiness": status,
    }


@app.get("/api/health/live")
async def health_live() -> dict[str, Any]:
    return live_status()


@app.get("/api/health/ready")
async def health_ready() -> Response:
    status = readiness_status()
    return JSONResponse(status, status_code=200 if status["ok"] else 503)


@app.get("/api/auth/me")
async def auth_me(request: Request) -> dict[str, Any]:
    return auth_status(request)


@app.post("/api/auth/login")
async def auth_login(payload: LoginRequest, request: Request) -> Response:
    try:
        client = request.client.host if request.client else "unknown"
        session = login(payload.username, payload.password, client)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    response = JSONResponse({"authenticated": True, "username": payload.username})
    set_session_cookie(response, session)
    return response


@app.post("/api/auth/logout")
async def auth_logout() -> Response:
    response = JSONResponse({"authenticated": False})
    clear_session_cookie(response)
    return response


@app.get("/api/settings")
async def settings() -> dict[str, Any]:
    return {
        "host": CONFIG.host,
        "port": CONFIG.port,
        "data_dir": str(CONFIG.data_dir),
        "reports_dir": str(CONFIG.reports_dir),
        "logs_dir": str(CONFIG.logs_dir),
        "transcode_staging_dir": str(CONFIG.transcoder.staging_dir),
        "transcode_backup_dir": str(CONFIG.transcoder.backup_dir),
        "scan_concurrency": CONFIG.scanner.concurrency,
        "transcode_concurrency": CONFIG.transcoder.concurrency,
        "allowed_browse_roots": [str(path) for path in CONFIG.allowed_browse_roots],
        "default_extensions": DEFAULT_EXTENSIONS,
        "default_excludes": DEFAULT_EXCLUDES,
        "auth": {
            "mode": CONFIG.auth.mode,
            "admin_username": CONFIG.auth.admin_username if CONFIG.auth.mode == "single_admin" else None,
            "admin_password_configured": bool(CONFIG.auth.admin_password or CONFIG.auth.admin_password_hash),
            "session_secret_configured": bool(CONFIG.auth.session_secret),
            "cookie_secure": CONFIG.auth.cookie_secure,
        },
        "operations": {
            "allowed_origins": CONFIG.operations.allowed_origins,
            "readiness_min_free_bytes": CONFIG.operations.readiness_min_free_bytes,
            "log_retention_days": CONFIG.operations.log_retention_days,
            "staged_output_retention_days": CONFIG.operations.staged_output_retention_days,
        },
        "config_warnings": CONFIG.config_warnings,
    }


@app.get("/api/admin/status")
async def get_admin_status() -> dict[str, Any]:
    return admin_status()


@app.get("/api/admin/diagnostics")
async def get_admin_diagnostics() -> Response:
    return JSONResponse(
        diagnostics_status(),
        headers={"Content-Disposition": 'attachment; filename="media-atlas-diagnostics.json"'},
    )


@app.get("/api/admin/stats")
async def get_admin_stats() -> dict[str, Any]:
    return metrics_status()


@app.get("/api/admin/database-backup")
async def download_database_backup() -> FileResponse:
    backup_path = db.create_database_backup()
    return FileResponse(
        backup_path,
        media_type="application/vnd.sqlite3",
        filename=backup_path.name,
    )


@app.post("/api/admin/retention/run")
async def run_retention() -> dict[str, Any]:
    return apply_retention()


@app.get("/api/plex/settings")
async def plex_settings() -> dict[str, Any]:
    return get_plex_settings(include_secret=False)


@app.put("/api/plex/settings")
async def update_plex_settings(payload: PlexSettingsUpdate) -> dict[str, Any]:
    data = payload.model_dump(exclude_unset=True)
    return save_plex_settings(data)


@app.post("/api/plex/test-connection")
async def test_plex_connection() -> dict[str, Any]:
    try:
        return await plex_manager.test_connection()
    except PlexError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/plex/status")
async def plex_status() -> dict[str, Any]:
    return plex_status_summary()


@app.get("/api/plex/libraries")
async def plex_libraries(refresh: bool = False) -> list[dict[str, Any]]:
    if refresh:
        try:
            return await plex_manager.refresh_libraries()
        except PlexError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return stored_plex_libraries()


@app.post("/api/plex/sync")
async def start_plex_sync() -> dict[str, Any]:
    try:
        return await plex_manager.start_sync()
    except PlexError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/plex/sync-jobs")
async def get_plex_sync_jobs(limit: int = Query(default=20, ge=1, le=100)) -> list[dict[str, Any]]:
    return list_plex_sync_jobs(limit)


@app.get("/api/plex/sync-jobs/{job_id}")
async def get_plex_sync_job(job_id: int) -> dict[str, Any]:
    job = read_plex_sync_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Plex sync job not found.")
    return job


@app.get("/api/plex/sync-jobs/{job_id}/events")
async def plex_sync_events(job_id: int) -> StreamingResponse:
    async def stream() -> Any:
        while True:
            job = read_plex_sync_job(job_id)
            if not job:
                yield "event: error\ndata: {\"detail\":\"Plex sync job not found\"}\n\n"
                return
            yield f"data: {json.dumps(job)}\n\n"
            if job["status"] not in {"queued", "running"}:
                return
            await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/plex/sync-jobs/{job_id}/cancel")
async def cancel_plex_sync(job_id: int) -> dict[str, Any]:
    plex_manager.cancel_sync(job_id)
    return await get_plex_sync_job(job_id)


@app.post("/api/plex/sync-jobs/{job_id}/retry")
async def retry_plex_sync(job_id: int) -> dict[str, Any]:
    try:
        return await plex_manager.retry_sync(job_id)
    except PlexError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/plex/unmatched")
async def get_plex_unmatched(limit: int = Query(default=200, ge=1, le=1000)) -> dict[str, Any]:
    return plex_unmatched(limit)


@app.get("/api/retention/settings")
async def media_retention_settings() -> dict[str, Any]:
    return get_media_retention_settings()


@app.put("/api/retention/settings")
async def update_media_retention_settings(payload: RetentionSettingsUpdate) -> dict[str, Any]:
    try:
        return save_media_retention_settings(payload.model_dump(exclude_unset=True))
    except MediaRetentionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/retention/connections")
async def get_retention_connections() -> list[dict[str, Any]]:
    return list_retention_connections()


@app.post("/api/retention/connections")
async def add_retention_connection(payload: RetentionConnectionCreate) -> dict[str, Any]:
    try:
        return create_retention_connection(payload.model_dump())
    except MediaRetentionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/retention/connections/{connection_id}")
async def patch_retention_connection(
    connection_id: int, payload: RetentionConnectionUpdate
) -> dict[str, Any]:
    try:
        return update_retention_connection(connection_id, payload.model_dump(exclude_unset=True))
    except MediaRetentionError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc).lower() else 400, detail=str(exc)) from exc


@app.delete("/api/retention/connections/{connection_id}")
async def remove_retention_connection(connection_id: int) -> dict[str, Any]:
    try:
        delete_retention_connection(connection_id)
        return {"deleted": True, "id": connection_id}
    except MediaRetentionError as exc:
        raise HTTPException(status_code=409 if "history" in str(exc).lower() else 404, detail=str(exc)) from exc


@app.post("/api/retention/connections/{connection_id}/test")
async def test_retention_connection(connection_id: int) -> dict[str, Any]:
    try:
        return await media_retention_manager.test_connection(connection_id)
    except MediaRetentionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/retention/summary")
async def get_retention_summary() -> dict[str, Any]:
    return retention_summary()


@app.post("/api/retention/analyses")
async def start_retention_analysis() -> dict[str, Any]:
    try:
        return await media_retention_manager.start_analysis("manual")
    except MediaRetentionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/retention/analyses")
async def get_retention_analyses(limit: int = Query(default=30, ge=1, le=200)) -> list[dict[str, Any]]:
    return list_retention_analysis_jobs(limit)


@app.get("/api/retention/analyses/{job_id}")
async def get_retention_analysis(job_id: int) -> dict[str, Any]:
    job = read_retention_analysis_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Retention analysis job not found.")
    return job


@app.get("/api/retention/analyses/{job_id}/events")
async def retention_analysis_events(job_id: int) -> StreamingResponse:
    async def stream() -> Any:
        while True:
            job = read_retention_analysis_job(job_id)
            if not job:
                yield "event: error\ndata: {\"detail\":\"Retention analysis job not found\"}\n\n"
                return
            yield f"data: {json.dumps(job)}\n\n"
            if job["status"] not in {"queued", "running"}:
                return
            await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/retention/analyses/{job_id}/cancel")
async def cancel_retention_analysis(job_id: int) -> dict[str, Any]:
    try:
        media_retention_manager.cancel_analysis(job_id)
    except MediaRetentionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return await get_retention_analysis(job_id)


@app.post("/api/retention/analyses/{job_id}/retry")
async def retry_retention_analysis(job_id: int) -> dict[str, Any]:
    try:
        return await media_retention_manager.retry_analysis(job_id)
    except MediaRetentionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/retention/candidates")
async def get_retention_candidates(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    status: str | None = None,
    media_type: str | None = None,
    connection_id: int | None = None,
    query: str | None = None,
    sort: str = "size_bytes",
    direction: str = "desc",
) -> dict[str, Any]:
    return list_retention_candidates(
        page=page,
        page_size=page_size,
        status=status,
        media_type=media_type,
        connection_id=connection_id,
        query=query,
        sort=sort,
        direction=direction,
    )


@app.get("/api/retention/candidates/{candidate_id}")
async def get_retention_candidate(candidate_id: int) -> dict[str, Any]:
    candidate = read_retention_candidate(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Retention candidate not found.")
    return candidate


@app.post("/api/retention/candidates/{candidate_id}/transcode-plan")
async def create_retention_transcode_plan(
    candidate_id: int, payload: RetentionTranscodePlanCreate, request: Request
) -> dict[str, Any]:
    try:
        return create_candidate_transcode_plan(
            candidate_id,
            payload.profile_id,
            payload.file_ids,
            payload.name,
            authenticated_user(request),
        )
    except MediaRetentionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/retention/candidates/{candidate_id}/delete")
async def remove_retention_candidate(
    candidate_id: int, payload: RetentionDeleteRequest, request: Request
) -> dict[str, Any]:
    try:
        return await delete_retention_candidate(
            candidate_id,
            payload.confirmation_text,
            authenticated_user(request),
            plex_manager=plex_manager,
        )
    except AmbiguousDeleteError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MediaRetentionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/retention/actions")
async def get_retention_actions(
    limit: int = Query(default=100, ge=1, le=500), candidate_id: int | None = None
) -> list[dict[str, Any]]:
    return list_retention_actions(limit, candidate_id)


@app.post("/api/retention/actions/{action_id}/retry-seerr")
async def retry_retention_seerr(action_id: int, request: Request) -> dict[str, Any]:
    try:
        return await retry_seerr_reconciliation(action_id, authenticated_user(request))
    except MediaRetentionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/retention/retention-candidates.csv")
async def export_retention_candidates() -> Response:
    return _csv_response("retention-candidates.csv", candidate_export_rows())


@app.get("/api/roots")
async def list_roots() -> list[dict[str, Any]]:
    return [_inflate_root(row) for row in db.query_all("SELECT * FROM media_roots ORDER BY name")]


@app.post("/api/roots")
async def create_root(payload: RootCreate) -> dict[str, Any]:
    try:
        path = resolve_existing_directory(payload.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not os.access(path, os.R_OK):
        raise HTTPException(status_code=400, detail="Path is not readable.")
    now = db.utc_now()
    try:
        root_id = db.execute(
            """
            INSERT INTO media_roots (
                name, path, enabled, include_extensions_json, exclude_patterns_json,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.name,
                str(path),
                1 if payload.enabled else 0,
                db.dumps(payload.include_extensions),
                db.dumps(payload.exclude_patterns),
                now,
                now,
            ),
        )
    except Exception as exc:
        raise HTTPException(status_code=409, detail=f"Could not create root: {exc}") from exc
    return _inflate_root(db.query_one("SELECT * FROM media_roots WHERE id = ?", (root_id,)) or {})


@app.patch("/api/roots/{root_id}")
async def update_root(root_id: int, payload: RootUpdate) -> dict[str, Any]:
    root = db.query_one("SELECT * FROM media_roots WHERE id = ?", (root_id,))
    if not root:
        raise HTTPException(status_code=404, detail="Root not found.")
    updates: dict[str, Any] = {}
    if payload.name is not None:
        updates["name"] = payload.name
    if payload.path is not None:
        try:
            updates["path"] = str(resolve_existing_directory(payload.path))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payload.enabled is not None:
        updates["enabled"] = 1 if payload.enabled else 0
    if payload.include_extensions is not None:
        updates["include_extensions_json"] = db.dumps(payload.include_extensions)
    if payload.exclude_patterns is not None:
        updates["exclude_patterns_json"] = db.dumps(payload.exclude_patterns)
    if not updates:
        return _inflate_root(root)
    updates["updated_at"] = db.utc_now()
    assignments = ", ".join(f"{column} = ?" for column in updates)
    db.execute(
        f"UPDATE media_roots SET {assignments} WHERE id = ?",
        tuple(updates.values()) + (root_id,),
    )
    return _inflate_root(db.query_one("SELECT * FROM media_roots WHERE id = ?", (root_id,)) or {})


@app.delete("/api/roots/{root_id}")
async def delete_root(root_id: int) -> dict[str, Any]:
    root = db.query_one("SELECT * FROM media_roots WHERE id = ?", (root_id,))
    if not root:
        raise HTTPException(status_code=404, detail="Root not found.")
    files = db.query_all("SELECT id FROM files WHERE root_id = ?", (root_id,))
    with db.connect() as connection:
        for file_row in files:
            connection.execute("DELETE FROM streams WHERE file_id = ?", (file_row["id"],))
            connection.execute("DELETE FROM chapters WHERE file_id = ?", (file_row["id"],))
        connection.execute("DELETE FROM files WHERE root_id = ?", (root_id,))
        connection.execute("DELETE FROM media_roots WHERE id = ?", (root_id,))
    return {"deleted": True}


@app.get("/api/directory-browser")
async def directory_browser(path: str | None = None) -> dict[str, Any]:
    if not CONFIG.directory_browser_enabled:
        raise HTTPException(status_code=404, detail="Directory browser is disabled.")
    requested = Path(path).expanduser().resolve() if path else CONFIG.allowed_browse_roots[0]
    if not is_within_allowed_browse_roots(requested):
        raise HTTPException(status_code=403, detail="Path is outside allowed browse roots.")
    if not requested.exists() or not requested.is_dir():
        raise HTTPException(status_code=400, detail="Path is not an existing directory.")
    directories = []
    try:
        for item in sorted(requested.iterdir(), key=lambda child: child.name.lower()):
            if item.is_dir():
                directories.append({"name": item.name, "path": str(item), "readable": os.access(item, os.R_OK)})
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    parent = requested.parent if requested.parent != requested else None
    return {
        "path": str(requested),
        "parent": str(parent) if parent and is_within_allowed_browse_roots(parent) else None,
        "directories": directories,
        "allowed_roots": [str(root) for root in CONFIG.allowed_browse_roots],
    }


@app.post("/api/scans")
async def start_scan() -> dict[str, Any]:
    return await scan_manager.start_scan()


@app.get("/api/scans")
async def list_scans(limit: int = Query(default=20, ge=1, le=100)) -> list[dict[str, Any]]:
    return db.query_all("SELECT * FROM scan_jobs ORDER BY id DESC LIMIT ?", (limit,))


@app.get("/api/scans/{scan_id}")
async def get_scan(scan_id: int) -> dict[str, Any]:
    scan = db.query_one("SELECT * FROM scan_jobs WHERE id = ?", (scan_id,))
    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found.")
    scan["errors"] = db.query_all("SELECT * FROM scan_errors WHERE scan_job_id = ? ORDER BY id DESC", (scan_id,))
    return scan


@app.post("/api/scans/{scan_id}/cancel")
async def cancel_scan(scan_id: int) -> dict[str, Any]:
    scan_manager.cancel_scan(scan_id)
    return db.query_one("SELECT * FROM scan_jobs WHERE id = ?", (scan_id,)) or {"id": scan_id}


@app.post("/api/scans/{scan_id}/retry")
async def retry_scan(scan_id: int) -> dict[str, Any]:
    try:
        return await scan_manager.retry_scan(scan_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/scans/{scan_id}/events")
async def scan_events(scan_id: int) -> StreamingResponse:
    async def stream() -> Any:
        while True:
            scan = db.query_one("SELECT * FROM scan_jobs WHERE id = ?", (scan_id,))
            if not scan:
                yield "event: error\ndata: {\"detail\":\"Scan not found\"}\n\n"
                return
            yield f"data: {json.dumps(scan)}\n\n"
            if scan["status"] not in {"queued", "running"}:
                return
            await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/media")
async def list_media(
    query: str | None = None,
    root_id: int | None = None,
    extension: str | None = None,
    container: str | None = None,
    video_codec: str | None = None,
    audio_codec: str | None = None,
    resolution: str | None = None,
    recommendation_category: str | None = None,
    is_missing: bool | None = None,
    plex_matched: bool | None = None,
    plex_library: str | None = None,
    plex_type: str | None = None,
    plex_year: int | None = None,
    plex_collection: str | None = None,
    plex_genre: str | None = None,
    plex_label: str | None = None,
    plex_watched: bool | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    sort: str = "filename",
    direction: Literal["asc", "desc"] = "asc",
) -> dict[str, Any]:
    where, params = _media_filters(
        query,
        root_id,
        extension,
        container,
        video_codec,
        audio_codec,
        resolution,
        recommendation_category,
        is_missing,
        plex_matched,
        plex_library,
        plex_type,
        plex_year,
        plex_collection,
        plex_genre,
        plex_label,
        plex_watched,
    )
    sort_column = _sort_column(sort)
    offset = (page - 1) * page_size
    joins = plex_join_clause()
    total = db.query_one(f"SELECT COUNT(*) AS total FROM files f {joins} {where}", tuple(params)) or {"total": 0}
    items = db.query_all(
        f"""
        SELECT f.*, r.name AS root_name, {plex_select_columns()}
        FROM files f
        LEFT JOIN media_roots r ON r.id = f.root_id
        {joins}
        {where}
        ORDER BY {sort_column} {direction.upper()}, f.id ASC
        LIMIT ? OFFSET ?
        """,
        tuple(params) + (page_size, offset),
    )
    return {"items": [_inflate_file(row) for row in items], "page": page, "page_size": page_size, "total": total["total"]}


@app.get("/api/media/{file_id}")
async def get_media(file_id: int) -> dict[str, Any]:
    row = db.query_one(
        f"""
        SELECT f.*, r.name AS root_name, {plex_select_columns()}
        FROM files f
        LEFT JOIN media_roots r ON r.id = f.root_id
        {plex_join_clause()}
        WHERE f.id = ?
        """,
        (file_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Media file not found.")
    item = _inflate_file(row)
    item["streams"] = db.query_all("SELECT * FROM streams WHERE file_id = ? ORDER BY stream_index", (file_id,))
    item["chapters"] = db.query_all("SELECT * FROM chapters WHERE file_id = ? ORDER BY chapter_index", (file_id,))
    return item


@app.get("/api/reports/summary")
async def report_summary() -> dict[str, Any]:
    totals = db.query_one(
        """
        SELECT COUNT(*) AS total_files,
               COALESCE(SUM(size_bytes), 0) AS total_size_bytes,
               COALESCE(SUM(duration_seconds), 0) AS total_duration_seconds
        FROM files
        """
    ) or {}
    return {
        **totals,
        "by_video_codec": _group_report("primary_video_codec"),
        "by_container": _group_report("container"),
        "by_resolution": _group_report("resolution_bucket"),
        "by_audio_codec": _group_report("primary_audio_codec"),
        "by_recommendation": _group_report("recommendation_category"),
        "plex": plex_status_summary(),
        "largest_files": db.query_all(
            """
            SELECT id, filename, path, size_bytes, primary_video_codec, resolution_bucket, recommendation_category
            FROM files
            ORDER BY size_bytes DESC
            LIMIT 10
            """
        ),
        "recent_errors": db.query_all("SELECT * FROM scan_errors ORDER BY id DESC LIMIT 10"),
    }


@app.get("/api/reports/{report_name}")
async def named_report(report_name: str) -> dict[str, Any]:
    mapping = {
        "video-codecs": "primary_video_codec",
        "containers": "container",
        "resolutions": "resolution_bucket",
        "audio-codecs": "primary_audio_codec",
        "recommendations": "recommendation_category",
    }
    if report_name == "largest-files":
        return {"items": db.query_all("SELECT * FROM files ORDER BY size_bytes DESC LIMIT 100")}
    if report_name == "errors":
        return {"items": db.query_all("SELECT * FROM scan_errors ORDER BY id DESC LIMIT 200")}
    if report_name == "candidates":
        return {
            "items": db.query_all(
                """
                SELECT * FROM files
                WHERE recommendation_category IN ('Easy Win', 'Remux Only', 'Review')
                ORDER BY size_bytes DESC
                LIMIT 500
                """
            )
        }
    column = mapping.get(report_name)
    if not column:
        raise HTTPException(status_code=404, detail="Report not found.")
    return {"items": _group_report(column)}


@app.get("/api/exports/{export_name}")
async def export_csv(export_name: str) -> Response:
    if export_name == "all-files.csv":
        rows = db.query_all("SELECT * FROM files ORDER BY path")
    elif export_name == "transcode-candidates.csv":
        rows = db.query_all(
            """
            SELECT * FROM files
            WHERE recommendation_category IN ('Easy Win', 'Remux Only', 'Review')
            ORDER BY recommendation_category, size_bytes DESC
            """
        )
    elif export_name == "scan-errors.csv":
        rows = db.query_all("SELECT * FROM scan_errors ORDER BY id DESC")
    elif export_name == "summary-by-codec.csv":
        rows = _group_report("primary_video_codec")
    elif export_name == "summary-by-container.csv":
        rows = _group_report("container")
    elif export_name == "summary-by-resolution.csv":
        rows = _group_report("resolution_bucket")
    elif export_name == "largest-files.csv":
        rows = db.query_all("SELECT * FROM files ORDER BY size_bytes DESC LIMIT 500")
    elif export_name == "retention-candidates.csv":
        rows = candidate_export_rows()
    else:
        raise HTTPException(status_code=404, detail="Export not found.")
    return _csv_response(export_name, rows)


@app.get("/api/transcode-profiles")
async def transcode_profiles() -> list[dict[str, Any]]:
    return db.query_all("SELECT * FROM transcode_profiles ORDER BY id")


@app.post("/api/transcode-plans")
async def create_transcode_plan(payload: PlanCreate) -> dict[str, Any]:
    try:
        return create_plan(payload.name, payload.profile_id, payload.file_ids, payload.notes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/transcode-plans")
async def list_transcode_plans(include_archived: bool = False) -> list[dict[str, Any]]:
    archive_filter = "" if include_archived else "WHERE tp.archived_at IS NULL"
    plans = db.query_all(
        f"""
        SELECT tp.*, p.name AS profile_name,
               (SELECT COUNT(*) FROM transcode_plan_items WHERE plan_id = tp.id) AS item_count,
               (SELECT COUNT(*) FROM transcode_runs WHERE plan_id = tp.id) AS run_count
        FROM transcode_plans tp
        LEFT JOIN transcode_profiles p ON p.id = tp.profile_id
        {archive_filter}
        ORDER BY tp.id DESC
        """
    )
    for plan in plans:
        plan["sample_items"] = db.query_all(
            """
            SELECT tpi.id, tpi.file_id, tpi.source_path, tpi.target_path, tpi.action,
                   tpi.reason, f.filename
            FROM transcode_plan_items tpi
            LEFT JOIN files f ON f.id = tpi.file_id
            WHERE tpi.plan_id = ?
            ORDER BY tpi.id
            LIMIT 6
            """,
            (plan["id"],),
        )
        plan["latest_run"] = db.query_one(
            """
            SELECT id, name, status, created_at, started_at, finished_at,
                   total_items, completed_items, failed_items, canceled_items,
                   progress_percent
            FROM transcode_runs
            WHERE plan_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (plan["id"],),
        )
    return plans


@app.post("/api/transcode-plans/{plan_id}/archive")
async def archive_transcode_plan(plan_id: int) -> dict[str, Any]:
    plan = db.query_one("SELECT * FROM transcode_plans WHERE id = ?", (plan_id,))
    if not plan:
        raise HTTPException(status_code=404, detail="Transcode plan not found.")
    db.execute(
        "UPDATE transcode_plans SET archived_at = COALESCE(archived_at, ?), updated_at = ? WHERE id = ?",
        (db.utc_now(), db.utc_now(), plan_id),
    )
    return await read_transcode_plan(plan_id)


@app.post("/api/transcode-plans/{plan_id}/unarchive")
async def unarchive_transcode_plan(plan_id: int) -> dict[str, Any]:
    plan = db.query_one("SELECT * FROM transcode_plans WHERE id = ?", (plan_id,))
    if not plan:
        raise HTTPException(status_code=404, detail="Transcode plan not found.")
    db.execute(
        "UPDATE transcode_plans SET archived_at = NULL, updated_at = ? WHERE id = ?",
        (db.utc_now(), plan_id),
    )
    return await read_transcode_plan(plan_id)


@app.delete("/api/transcode-plans/{plan_id}")
async def delete_transcode_plan(plan_id: int) -> dict[str, Any]:
    plan = db.query_one("SELECT * FROM transcode_plans WHERE id = ?", (plan_id,))
    if not plan:
        raise HTTPException(status_code=404, detail="Transcode plan not found.")
    run_count = db.query_one("SELECT COUNT(*) AS count FROM transcode_runs WHERE plan_id = ?", (plan_id,))
    if run_count and run_count["count"]:
        raise HTTPException(
            status_code=409,
            detail="Plans with transcode run history cannot be deleted. Archive the plan instead.",
        )
    db.execute("DELETE FROM transcode_plans WHERE id = ?", (plan_id,))
    return {"deleted": True, "id": plan_id}


@app.get("/api/transcode-plans/{plan_id}")
async def read_transcode_plan(plan_id: int) -> dict[str, Any]:
    try:
        return get_plan(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/transcode-plans/{plan_id}/download.csv")
async def download_plan_csv(plan_id: int) -> Response:
    plan = await read_transcode_plan(plan_id)
    return _csv_response(f"transcode-plan-{plan_id}.csv", plan["items"])


@app.get("/api/transcode-plans/{plan_id}/download.sh")
async def download_plan_shell(plan_id: int) -> Response:
    plan = await read_transcode_plan(plan_id)
    lines = ["#!/usr/bin/env bash", "set -euo pipefail", ""]
    for item in plan["items"]:
        if item.get("command_display"):
            lines.append(item["command_display"])
    body = "\n".join(lines) + "\n"
    return Response(
        body,
        media_type="text/x-shellscript",
        headers={"Content-Disposition": f'attachment; filename="transcode-plan-{plan_id}.sh"'},
    )


@app.post("/api/transcode-runs")
async def create_transcode_run(payload: RunCreate) -> dict[str, Any]:
    try:
        return await transcode_manager.create_run(payload.plan_id, payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/transcode-runs")
async def list_transcode_runs(
    limit: int = Query(default=50, ge=1, le=200),
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    archive_filter = "" if include_archived else "WHERE archived_at IS NULL"
    return db.query_all(
        f"""
        SELECT *
        FROM transcode_runs
        {archive_filter}
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    )
@app.get("/api/transcode-runs/stats")
async def transcode_run_stats() -> dict[str, Any]:
    return transcode_savings_stats()


@app.get("/api/transcode-runs/{run_id}")
async def read_transcode_run(run_id: int) -> dict[str, Any]:
    run = db.query_one("SELECT * FROM transcode_runs WHERE id = ?", (run_id,))
    if not run:
        raise HTTPException(status_code=404, detail="Transcode run not found.")
    run["items"] = db.query_all("SELECT * FROM transcode_run_items WHERE run_id = ? ORDER BY id", (run_id,))
    return run


@app.post("/api/transcode-runs/{run_id}/cancel")
async def cancel_transcode_run(run_id: int) -> dict[str, Any]:
    transcode_manager.cancel_run(run_id)
    return await read_transcode_run(run_id)


@app.post("/api/transcode-runs/{run_id}/retry")
async def retry_transcode_run(run_id: int) -> dict[str, Any]:
    await transcode_manager.retry_run(run_id)
    return await read_transcode_run(run_id)


@app.post("/api/transcode-runs/{run_id}/archive")
async def archive_transcode_run(run_id: int) -> dict[str, Any]:
    run = db.query_one("SELECT * FROM transcode_runs WHERE id = ?", (run_id,))
    if not run:
        raise HTTPException(status_code=404, detail="Transcode run not found.")
    if run["status"] in {"queued", "running"}:
        raise HTTPException(status_code=400, detail="Active transcode runs cannot be archived.")
    db.execute(
        "UPDATE transcode_runs SET archived_at = COALESCE(archived_at, ?) WHERE id = ?",
        (db.utc_now(), run_id),
    )
    return await read_transcode_run(run_id)


@app.post("/api/transcode-runs/{run_id}/unarchive")
async def unarchive_transcode_run(run_id: int) -> dict[str, Any]:
    run = db.query_one("SELECT * FROM transcode_runs WHERE id = ?", (run_id,))
    if not run:
        raise HTTPException(status_code=404, detail="Transcode run not found.")
    db.execute("UPDATE transcode_runs SET archived_at = NULL WHERE id = ?", (run_id,))
    return await read_transcode_run(run_id)


@app.post("/api/transcode-runs/{run_id}/cleanup")
async def cleanup_transcode_run(run_id: int, payload: CleanupRunRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            transcode_manager.cleanup_run_artifacts,
            run_id,
            payload.confirmation_text,
            payload.archive_run,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/transcode-runs/{run_id}/items/{item_id}/cleanup")
async def cleanup_transcode_item(run_id: int, item_id: int, payload: CleanupRunItemRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            transcode_manager.cleanup_item_artifacts,
            run_id,
            item_id,
            payload.confirmation_text,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/transcode-runs/{run_id}/items/{item_id}/validate")
async def validate_transcode_item(run_id: int, item_id: int, payload: ValidateRunItemRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            transcode_manager.validate_item,
            run_id,
            item_id,
            payload.confirmation_text,
            payload.message,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/transcode-runs/{run_id}/events")
async def transcode_run_events(run_id: int) -> StreamingResponse:
    async def stream() -> Any:
        while True:
            run = await read_transcode_run(run_id)
            yield f"data: {json.dumps(run)}\n\n"
            if run["status"] not in {"queued", "running"}:
                return
            await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/transcode-runs/{run_id}/items/{item_id}/log", response_class=PlainTextResponse)
async def transcode_item_log(run_id: int, item_id: int) -> str:
    item = db.query_one("SELECT * FROM transcode_run_items WHERE id = ? AND run_id = ?", (item_id, run_id))
    if not item:
        raise HTTPException(status_code=404, detail="Run item not found.")
    log_path = item.get("log_path")
    if not log_path or not Path(log_path).exists():
        return ""
    text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    return text[-20000:]


@app.post("/api/transcode-runs/{run_id}/items/{item_id}/publish")
async def publish_transcode_item(run_id: int, item_id: int, payload: PublishRunItemRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            transcode_manager.publish_item,
            run_id,
            item_id,
            payload.source_path,
            payload.target_path,
            payload.confirmation_text,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _inflate_root(row: dict[str, Any]) -> dict[str, Any]:
    row = dict(row)
    row["enabled"] = bool(row.get("enabled"))
    row["include_extensions"] = db.loads_json(row.pop("include_extensions_json", None), DEFAULT_EXTENSIONS)
    row["exclude_patterns"] = db.loads_json(row.pop("exclude_patterns_json", None), DEFAULT_EXCLUDES)
    return row


def _inflate_file(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["is_missing"] = bool(item.get("is_missing"))
    item["is_hdr"] = bool(item.get("is_hdr"))
    item["is_interlaced"] = bool(item.get("is_interlaced"))
    item["has_forced_subtitles"] = bool(item.get("has_forced_subtitles"))
    item["has_image_subtitles"] = bool(item.get("has_image_subtitles"))
    item["recommendation_reasons"] = db.loads_json(item.pop("recommendation_reasons_json", None), [])
    item["recommendation_warnings"] = db.loads_json(item.pop("recommendation_warnings_json", None), [])
    inflate_plex(item)
    return item


def _media_filters(*values: Any) -> tuple[str, list[Any]]:
    (
        query,
        root_id,
        extension,
        container,
        video_codec,
        audio_codec,
        resolution,
        recommendation_category,
        is_missing,
        plex_matched,
        plex_library,
        plex_type,
        plex_year,
        plex_collection,
        plex_genre,
        plex_label,
        plex_watched,
    ) = values
    conditions: list[str] = []
    params: list[Any] = []
    if query:
        like = f"%{query}%"
        conditions.append(
            """
            (
                f.path LIKE ? OR f.filename LIKE ? OR f.directory LIKE ? OR
                f.recommendation_summary LIKE ? OR pi.title LIKE ? OR
                pi.show_title LIKE ? OR pi.sort_title LIKE ? OR pi.summary LIKE ?
            )
            """
        )
        params.extend([like, like, like, like, like, like, like, like])
    simple_filters = [
        ("f.root_id", root_id),
        ("f.extension", extension),
        ("f.container", container),
        ("f.primary_video_codec", video_codec),
        ("f.primary_audio_codec", audio_codec),
        ("f.resolution_bucket", resolution),
        ("f.recommendation_category", recommendation_category),
    ]
    for column, value in simple_filters:
        if value not in (None, ""):
            conditions.append(f"{column} = ?")
            params.append(value)
    if is_missing is not None:
        conditions.append("f.is_missing = ?")
        params.append(1 if is_missing else 0)
    if plex_matched is not None:
        conditions.append("pfm.match_status = 'matched'" if plex_matched else "(pfm.id IS NULL OR pfm.match_status != 'matched')")
    if plex_library:
        conditions.append("pi.library_section_key = ?")
        params.append(plex_library)
    if plex_type:
        conditions.append("pi.type = ?")
        params.append(plex_type)
    if plex_year is not None:
        conditions.append("pi.year = ?")
        params.append(plex_year)
    json_filters = [
        ("pi.collections_json", plex_collection),
        ("pi.genres_json", plex_genre),
        ("pi.labels_json", plex_label),
    ]
    for column, value in json_filters:
        if value:
            conditions.append(f"{column} LIKE ?")
            params.append(f"%{value}%")
    if plex_watched is not None:
        conditions.append("COALESCE(pi.view_count, 0) > 0" if plex_watched else "COALESCE(pi.view_count, 0) = 0")
    return ("WHERE " + " AND ".join(conditions) if conditions else ""), params


def _sort_column(sort: str) -> str:
    allowed = {
        "filename": "f.filename",
        "size_bytes": "f.size_bytes",
        "duration_seconds": "f.duration_seconds",
        "bitrate_mbps": "f.bitrate_mbps",
        "resolution": "f.height",
        "video_codec": "f.primary_video_codec",
        "container": "f.container",
        "modified_time": "f.modified_time_ns",
        "last_scanned": "f.last_scanned_at",
        "recommendation": "f.recommendation_category",
        "plex_title": "pi.title",
        "plex_year": "pi.year",
        "plex_added": "pi.added_at",
    }
    return allowed.get(sort, "f.filename")


def _group_report(column: str) -> list[dict[str, Any]]:
    allowed = {
        "primary_video_codec",
        "container",
        "resolution_bucket",
        "primary_audio_codec",
        "recommendation_category",
    }
    if column not in allowed:
        raise HTTPException(status_code=400, detail="Invalid report column.")
    return db.query_all(
        f"""
        SELECT COALESCE({column}, 'Unknown') AS label,
               COUNT(*) AS file_count,
               COALESCE(SUM(size_bytes), 0) AS total_size_bytes,
               COALESCE(SUM(duration_seconds), 0) AS total_duration_seconds
        FROM files
        GROUP BY COALESCE({column}, 'Unknown')
        ORDER BY total_size_bytes DESC, file_count DESC
        """
    )


def _csv_response(filename: str, rows: list[dict[str, Any]]) -> Response:
    output = io.StringIO()
    if rows:
        writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    else:
        output.write("")
    return Response(
        output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
