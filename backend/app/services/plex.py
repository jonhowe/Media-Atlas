from __future__ import annotations

import asyncio
import os
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from typing import Any

from .. import db

SETTINGS_KEY = "plex_settings"

DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": False,
    "server_url": "",
    "token": "",
    "selected_library_keys": [],
    "timeout_seconds": 10,
    "path_mappings": [],
}


class PlexError(Exception):
    pass


class PlexClient:
    def __init__(self, settings: dict[str, Any]) -> None:
        self.server_url = str(settings.get("server_url") or "").rstrip("/")
        self.token = str(settings.get("token") or "")
        self.timeout = max(1, int(settings.get("timeout_seconds") or 10))
        if not self.server_url:
            raise PlexError("Plex server URL is not configured.")
        if not self.token:
            raise PlexError("Plex token is not configured.")

    async def libraries(self) -> list[dict[str, Any]]:
        data = await self._request("/library/sections")
        container = _media_container(data)
        return _as_list(container.get("Directory"))

    async def library_items(self, section_key: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        start = 0
        size = 200
        while True:
            data = await self._request(
                f"/library/sections/{section_key}/all",
                {
                    "includeGuids": "1",
                    "includeCollections": "1",
                    "includeLabels": "1",
                    "X-Plex-Container-Start": str(start),
                    "X-Plex-Container-Size": str(size),
                },
            )
            container = _media_container(data)
            batch = _as_list(container.get("Metadata") or container.get("Video") or container.get("Directory"))
            items.extend(batch)
            total_size = _as_int(container.get("totalSize")) or _as_int(container.get("TotalSize"))
            if not batch or len(batch) < size:
                break
            start += len(batch)
            if total_size is not None and start >= total_size:
                break
        return items

    async def _request(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        try:
            import httpx
        except ModuleNotFoundError as exc:
            raise PlexError("The httpx dependency is required for Plex integration.") from exc
        headers = {
            "Accept": "application/json",
            "X-Plex-Token": self.token,
            "X-Plex-Product": "Media Atlas",
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout, headers=headers) as client:
                response = await client.get(f"{self.server_url}{path}", params=params)
        except httpx.HTTPError as exc:
            raise PlexError(f"Plex request failed: {exc}") from exc
        if response.status_code == 401:
            raise PlexError("Plex rejected the configured token.")
        if response.status_code >= 400:
            raise PlexError(f"Plex returned HTTP {response.status_code}.")
        try:
            return response.json()
        except ValueError:
            try:
                return _xml_to_dict(response.text)
            except ET.ParseError as exc:
                raise PlexError("Plex returned a response that could not be parsed.") from exc


class PlexSyncManager:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def test_connection(self) -> dict[str, Any]:
        client = PlexClient(get_settings(include_secret=True))
        libraries = await client.libraries()
        return {
            "ok": True,
            "library_count": len(libraries),
            "libraries": [_library_payload(item) for item in libraries],
        }

    async def refresh_libraries(self) -> list[dict[str, Any]]:
        settings = get_settings(include_secret=True)
        client = PlexClient(settings)
        libraries = [_library_payload(item) for item in await client.libraries()]
        now = db.utc_now()
        with db.connect() as connection:
            for library in libraries:
                connection.execute(
                    """
                    INSERT INTO plex_libraries (
                        section_key, title, type, agent, scanner, language, uuid, updated_at, raw_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(section_key) DO UPDATE SET
                        title = excluded.title,
                        type = excluded.type,
                        agent = excluded.agent,
                        scanner = excluded.scanner,
                        language = excluded.language,
                        uuid = excluded.uuid,
                        updated_at = excluded.updated_at,
                        raw_json = excluded.raw_json
                    """,
                    (
                        library["section_key"],
                        library["title"],
                        library.get("type"),
                        library.get("agent"),
                        library.get("scanner"),
                        library.get("language"),
                        library.get("uuid"),
                        now,
                        db.dumps(library.get("raw") or {}),
                    ),
                )
        return stored_libraries()

    async def start_sync(self) -> dict[str, Any]:
        async with self._lock:
            settings = get_settings(include_secret=True)
            PlexClient(settings)
            active = db.query_one(
                "SELECT * FROM plex_sync_jobs WHERE status IN ('queued', 'running') ORDER BY id DESC LIMIT 1"
            )
            if active:
                return active
            job_id = db.execute(
                """
                INSERT INTO plex_sync_jobs (status, created_at, message)
                VALUES ('queued', ?, 'Plex sync queued.')
                """,
                (db.utc_now(),),
            )
            self._task = asyncio.create_task(self._run_sync(job_id))
            return read_sync_job(job_id) or {"id": job_id}

    def cancel_sync(self, job_id: int) -> None:
        db.execute(
            """
            UPDATE plex_sync_jobs
            SET cancel_requested = 1, message = 'Cancel requested.'
            WHERE id = ? AND status IN ('queued', 'running')
            """,
            (job_id,),
        )

    async def _run_sync(self, job_id: int) -> None:
        now = db.utc_now()
        db.execute(
            """
            UPDATE plex_sync_jobs
            SET status = 'running', started_at = ?, message = 'Fetching Plex libraries.'
            WHERE id = ?
            """,
            (now, job_id),
        )
        try:
            settings = get_settings(include_secret=True)
            selected = [str(item) for item in settings.get("selected_library_keys") or []]
            libraries = await self.refresh_libraries()
            if selected:
                libraries = [library for library in libraries if str(library["section_key"]) in selected]
            total_items = 0
            client = PlexClient(settings)
            db.execute("UPDATE plex_items SET is_stale = 1", ())
            for library in libraries:
                if _sync_canceled(job_id):
                    _finish_sync(job_id, "canceled", "Plex sync canceled.", None)
                    return
                db.execute(
                    """
                    UPDATE plex_sync_jobs
                    SET message = ?
                    WHERE id = ?
                    """,
                    (f"Fetching {library['title']}.", job_id),
                )
                items = await client.library_items(str(library["section_key"]))
                total_items += len(items)
                db.execute("UPDATE plex_sync_jobs SET total_items = ? WHERE id = ?", (total_items, job_id))
                for item in items:
                    if _sync_canceled(job_id):
                        _finish_sync(job_id, "canceled", "Plex sync canceled.", None)
                        return
                    _upsert_item(library, item, settings)
                    db.execute(
                        """
                        UPDATE plex_sync_jobs
                        SET processed_items = processed_items + 1
                        WHERE id = ?
                        """,
                        (job_id,),
                    )
            counts = rebuild_matches(settings)
            db.execute(
                """
                UPDATE plex_sync_jobs
                SET matched_files = ?,
                    unmatched_files = ?,
                    unmatched_parts = ?
                WHERE id = ?
                """,
                (counts["matched_files"], counts["unmatched_files"], counts["unmatched_parts"], job_id),
            )
            _finish_sync(job_id, "succeeded", "Plex sync complete.", None)
        except Exception as exc:
            _finish_sync(job_id, "failed", "Plex sync failed.", str(exc))


def get_settings(include_secret: bool = False) -> dict[str, Any]:
    row = db.query_one("SELECT value_json FROM app_settings WHERE key = ?", (SETTINGS_KEY,))
    stored = db.loads_json(row.get("value_json") if row else None, {})
    settings = {**DEFAULT_SETTINGS, **stored}
    settings["server_url"] = settings.get("server_url") or os.getenv("MEDIA_ATLAS_PLEX_URL", "")
    settings["token"] = settings.get("token") or os.getenv("MEDIA_ATLAS_PLEX_TOKEN", "")
    settings["selected_library_keys"] = [str(item) for item in settings.get("selected_library_keys") or []]
    settings["timeout_seconds"] = max(1, int(settings.get("timeout_seconds") or DEFAULT_SETTINGS["timeout_seconds"]))
    settings["path_mappings"] = _clean_mappings(settings.get("path_mappings") or [])
    if include_secret:
        return settings
    token = str(settings.get("token") or "")
    redacted = dict(settings)
    redacted.pop("token", None)
    redacted["token_configured"] = bool(token)
    redacted["token_hint"] = f"...{token[-4:]}" if token else ""
    return redacted


def save_settings(payload: dict[str, Any]) -> dict[str, Any]:
    existing = get_settings(include_secret=True)
    next_settings = {**existing}
    for key in ("enabled", "server_url", "selected_library_keys", "timeout_seconds", "path_mappings"):
        if key in payload and payload[key] is not None:
            next_settings[key] = payload[key]
    if payload.get("clear_token"):
        next_settings["token"] = ""
    elif payload.get("token"):
        next_settings["token"] = str(payload["token"])
    next_settings["server_url"] = str(next_settings.get("server_url") or "").rstrip("/")
    next_settings["selected_library_keys"] = [str(item) for item in next_settings.get("selected_library_keys") or []]
    next_settings["timeout_seconds"] = max(1, int(next_settings.get("timeout_seconds") or 10))
    next_settings["path_mappings"] = _clean_mappings(next_settings.get("path_mappings") or [])
    db.execute(
        """
        INSERT INTO app_settings (key, value_json, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value_json = excluded.value_json,
            updated_at = excluded.updated_at
        """,
        (SETTINGS_KEY, db.dumps(next_settings), db.utc_now()),
    )
    return get_settings(include_secret=False)


def stored_libraries() -> list[dict[str, Any]]:
    return db.query_all(
        """
        SELECT section_key, title, type, agent, scanner, language, uuid, updated_at
        FROM plex_libraries
        ORDER BY title
        """
    )


def read_sync_job(job_id: int) -> dict[str, Any] | None:
    return db.query_one("SELECT * FROM plex_sync_jobs WHERE id = ?", (job_id,))


def list_sync_jobs(limit: int = 20) -> list[dict[str, Any]]:
    return db.query_all("SELECT * FROM plex_sync_jobs ORDER BY id DESC LIMIT ?", (limit,))


def status_summary() -> dict[str, Any]:
    settings = get_settings(include_secret=False)
    latest = db.query_one("SELECT * FROM plex_sync_jobs ORDER BY id DESC LIMIT 1")
    matched = db.query_one("SELECT COUNT(*) AS count FROM plex_file_matches WHERE match_status = 'matched'") or {"count": 0}
    unmatched_files = db.query_one(
        """
        SELECT COUNT(*) AS count
        FROM files f
        LEFT JOIN plex_file_matches pfm ON pfm.file_id = f.id AND pfm.match_status = 'matched'
        WHERE f.is_missing = 0 AND pfm.id IS NULL
        """
    ) or {"count": 0}
    unmatched_parts = db.query_one(
        """
        SELECT COUNT(*) AS count
        FROM plex_media_parts pmp
        LEFT JOIN plex_file_matches pfm ON pfm.plex_media_part_id = pmp.id AND pfm.match_status = 'matched'
        WHERE pfm.id IS NULL
        """
    ) or {"count": 0}
    return {
        "configured": bool(settings.get("server_url") and settings.get("token_configured")),
        "enabled": bool(settings.get("enabled")),
        "server_url": settings.get("server_url"),
        "last_sync": latest,
        "matched_count": matched["count"],
        "unmatched_file_count": unmatched_files["count"],
        "unmatched_part_count": unmatched_parts["count"],
        "latest_error": latest.get("error_message") if latest else None,
    }


def unmatched(limit: int = 200) -> dict[str, Any]:
    files = db.query_all(
        """
        SELECT f.id, f.path, f.filename, f.size_bytes, f.primary_video_codec, f.resolution_bucket
        FROM files f
        LEFT JOIN plex_file_matches pfm ON pfm.file_id = f.id AND pfm.match_status = 'matched'
        WHERE f.is_missing = 0 AND pfm.id IS NULL
        ORDER BY f.path
        LIMIT ?
        """,
        (limit,),
    )
    parts = db.query_all(
        """
        SELECT pmp.id, pmp.file_path, pmp.normalized_path, pi.title, pi.show_title, pi.year, pi.type
        FROM plex_media_parts pmp
        JOIN plex_items pi ON pi.id = pmp.plex_item_id
        LEFT JOIN plex_file_matches pfm ON pfm.plex_media_part_id = pmp.id AND pfm.match_status = 'matched'
        WHERE pfm.id IS NULL
        ORDER BY pmp.normalized_path
        LIMIT ?
        """,
        (limit,),
    )
    return {"files": files, "parts": parts}


def rebuild_matches(settings: dict[str, Any] | None = None) -> dict[str, int]:
    settings = settings or get_settings(include_secret=True)
    mappings = _clean_mappings(settings.get("path_mappings") or [])
    now = db.utc_now()
    with db.connect() as connection:
        connection.execute("DELETE FROM plex_file_matches")
        for part in connection.execute("SELECT id, file_path FROM plex_media_parts").fetchall():
            normalized = apply_path_mappings(part["file_path"], mappings)
            connection.execute(
                "UPDATE plex_media_parts SET normalized_path = ? WHERE id = ?",
                (normalized, part["id"]),
            )
        files = connection.execute("SELECT id, path FROM files WHERE is_missing = 0").fetchall()
        for file_row in files:
            parts = connection.execute(
                """
                SELECT pmp.id AS part_id, pmp.plex_item_id
                FROM plex_media_parts pmp
                WHERE pmp.normalized_path = ?
                ORDER BY pmp.id
                """,
                (normalize_path(file_row["path"]),),
            ).fetchall()
            if len(parts) == 1:
                connection.execute(
                    """
                    INSERT INTO plex_file_matches (
                        file_id, plex_item_id, plex_media_part_id, match_status,
                        match_method, path_match_detail, matched_at
                    )
                    VALUES (?, ?, ?, 'matched', 'path', ?, ?)
                    """,
                    (file_row["id"], parts[0]["plex_item_id"], parts[0]["part_id"], "exact normalized path", now),
                )
            elif len(parts) > 1:
                connection.execute(
                    """
                    INSERT INTO plex_file_matches (
                        file_id, match_status, match_method, path_match_detail, matched_at
                    )
                    VALUES (?, 'ambiguous', 'path', ?, ?)
                    """,
                    (file_row["id"], f"{len(parts)} Plex parts have this normalized path", now),
                )
    matched = db.query_one("SELECT COUNT(*) AS count FROM plex_file_matches WHERE match_status = 'matched'") or {"count": 0}
    unmatched_files = db.query_one(
        """
        SELECT COUNT(*) AS count
        FROM files f
        LEFT JOIN plex_file_matches pfm ON pfm.file_id = f.id AND pfm.match_status = 'matched'
        WHERE f.is_missing = 0 AND pfm.id IS NULL
        """
    ) or {"count": 0}
    unmatched_parts = db.query_one(
        """
        SELECT COUNT(*) AS count
        FROM plex_media_parts pmp
        LEFT JOIN plex_file_matches pfm ON pfm.plex_media_part_id = pmp.id AND pfm.match_status = 'matched'
        WHERE pfm.id IS NULL
        """
    ) or {"count": 0}
    return {
        "matched_files": matched["count"],
        "unmatched_files": unmatched_files["count"],
        "unmatched_parts": unmatched_parts["count"],
    }


def plex_select_columns() -> str:
    return """
        pfm.match_status AS plex_match_status,
        pfm.match_method AS plex_match_method,
        pfm.path_match_detail AS plex_path_match_detail,
        pi.rating_key AS plex_rating_key,
        pi.guid AS plex_guid,
        pi.library_section_key AS plex_library_section_key,
        pi.library_section_title AS plex_library_section_title,
        pi.library_section_type AS plex_library_section_type,
        pi.type AS plex_type,
        pi.title AS plex_title,
        pi.sort_title AS plex_sort_title,
        pi.year AS plex_year,
        pi.show_title AS plex_show_title,
        pi.season_number AS plex_season_number,
        pi.episode_number AS plex_episode_number,
        pi.summary AS plex_summary,
        pi.content_rating AS plex_content_rating,
        pi.audience_rating AS plex_audience_rating,
        pi.user_rating AS plex_user_rating,
        pi.originally_available_at AS plex_originally_available_at,
        pi.added_at AS plex_added_at,
        pi.updated_at AS plex_updated_at,
        pi.last_viewed_at AS plex_last_viewed_at,
        pi.view_count AS plex_view_count,
        pi.thumb AS plex_thumb,
        pi.art AS plex_art,
        pi.collections_json AS plex_collections_json,
        pi.genres_json AS plex_genres_json,
        pi.labels_json AS plex_labels_json,
        pi.raw_json AS plex_raw_json,
        pmp.file_path AS plex_file_path,
        pmp.normalized_path AS plex_normalized_path
    """


def plex_join_clause() -> str:
    return """
        LEFT JOIN plex_file_matches pfm ON pfm.file_id = f.id
        LEFT JOIN plex_items pi ON pi.id = pfm.plex_item_id
        LEFT JOIN plex_media_parts pmp ON pmp.id = pfm.plex_media_part_id
    """


def inflate_plex(item: dict[str, Any]) -> None:
    plex_values: dict[str, Any] = {}
    for key in list(item.keys()):
        if key.startswith("plex_"):
            plex_values[key.removeprefix("plex_")] = item.pop(key)
    if not plex_values or not plex_values.get("match_status"):
        item["plex"] = None
        return
    plex_values["collections"] = db.loads_json(plex_values.pop("collections_json", None), [])
    plex_values["genres"] = db.loads_json(plex_values.pop("genres_json", None), [])
    plex_values["labels"] = db.loads_json(plex_values.pop("labels_json", None), [])
    plex_values["watched"] = bool(plex_values.get("view_count") and plex_values["view_count"] > 0)
    item["plex"] = plex_values


def apply_path_mappings(path: str, mappings: list[dict[str, str]]) -> str:
    normalized = normalize_path(path)
    ordered = sorted(mappings, key=lambda item: len(item["plex_path_prefix"]), reverse=True)
    for mapping in ordered:
        source = mapping["plex_path_prefix"]
        target = mapping["media_atlas_path_prefix"]
        if normalized == source or normalized.startswith(f"{source}/"):
            suffix = normalized[len(source) :]
            return normalize_path(f"{target}{suffix}")
    return normalized


def normalize_path(path: str) -> str:
    value = str(path or "").replace("\\", "/").strip()
    value = re.sub(r"/+", "/", value)
    if len(value) > 1:
        value = value.rstrip("/")
    return value


def _upsert_item(library: dict[str, Any], item: dict[str, Any], settings: dict[str, Any]) -> None:
    now = db.utc_now()
    payload = _item_payload(library, item)
    mappings = _clean_mappings(settings.get("path_mappings") or [])
    with db.connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO plex_items (
                rating_key, guid, library_section_key, library_section_title, library_section_type,
                type, title, sort_title, year, show_title, season_number, episode_number,
                summary, content_rating, audience_rating, user_rating, originally_available_at,
                added_at, updated_at, last_viewed_at, view_count, thumb, art,
                collections_json, genres_json, labels_json, raw_json, last_synced_at, is_stale
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(rating_key) DO UPDATE SET
                guid = excluded.guid,
                library_section_key = excluded.library_section_key,
                library_section_title = excluded.library_section_title,
                library_section_type = excluded.library_section_type,
                type = excluded.type,
                title = excluded.title,
                sort_title = excluded.sort_title,
                year = excluded.year,
                show_title = excluded.show_title,
                season_number = excluded.season_number,
                episode_number = excluded.episode_number,
                summary = excluded.summary,
                content_rating = excluded.content_rating,
                audience_rating = excluded.audience_rating,
                user_rating = excluded.user_rating,
                originally_available_at = excluded.originally_available_at,
                added_at = excluded.added_at,
                updated_at = excluded.updated_at,
                last_viewed_at = excluded.last_viewed_at,
                view_count = excluded.view_count,
                thumb = excluded.thumb,
                art = excluded.art,
                collections_json = excluded.collections_json,
                genres_json = excluded.genres_json,
                labels_json = excluded.labels_json,
                raw_json = excluded.raw_json,
                last_synced_at = excluded.last_synced_at,
                is_stale = 0
            """,
            (
                payload["rating_key"],
                payload.get("guid"),
                payload.get("library_section_key"),
                payload.get("library_section_title"),
                payload.get("library_section_type"),
                payload.get("type"),
                payload.get("title"),
                payload.get("sort_title"),
                payload.get("year"),
                payload.get("show_title"),
                payload.get("season_number"),
                payload.get("episode_number"),
                payload.get("summary"),
                payload.get("content_rating"),
                payload.get("audience_rating"),
                payload.get("user_rating"),
                payload.get("originally_available_at"),
                payload.get("added_at"),
                payload.get("updated_at"),
                payload.get("last_viewed_at"),
                payload.get("view_count"),
                payload.get("thumb"),
                payload.get("art"),
                db.dumps(payload.get("collections") or []),
                db.dumps(payload.get("genres") or []),
                db.dumps(payload.get("labels") or []),
                db.dumps(item),
                now,
            ),
        )
        row = connection.execute("SELECT id FROM plex_items WHERE rating_key = ?", (payload["rating_key"],)).fetchone()
        plex_item_id = row["id"] if row else cursor.lastrowid
        connection.execute("DELETE FROM plex_media_parts WHERE plex_item_id = ?", (plex_item_id,))
        for part in _parts_from_item(item):
            file_path = normalize_path(part["file_path"])
            connection.execute(
                """
                INSERT INTO plex_media_parts (
                    plex_item_id, part_id, file_path, normalized_path,
                    size_bytes, duration_ms, container, last_synced_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plex_item_id,
                    part.get("part_id"),
                    file_path,
                    apply_path_mappings(file_path, mappings),
                    part.get("size_bytes"),
                    part.get("duration_ms"),
                    part.get("container"),
                    now,
                ),
            )


def _library_payload(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "section_key": str(item.get("key") or item.get("sectionKey") or ""),
        "title": str(item.get("title") or item.get("Title") or "Untitled"),
        "type": item.get("type"),
        "agent": item.get("agent"),
        "scanner": item.get("scanner"),
        "language": item.get("language"),
        "uuid": item.get("uuid"),
        "raw": item,
    }


def _item_payload(library: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    return {
        "rating_key": str(item.get("ratingKey") or item.get("rating_key") or item.get("key") or ""),
        "guid": item.get("guid"),
        "library_section_key": str(library.get("section_key") or item.get("librarySectionKey") or ""),
        "library_section_title": library.get("title") or item.get("librarySectionTitle"),
        "library_section_type": library.get("type") or item.get("librarySectionType"),
        "type": item.get("type"),
        "title": item.get("title"),
        "sort_title": item.get("titleSort") or item.get("sortTitle"),
        "year": _as_int(item.get("year")),
        "show_title": item.get("grandparentTitle") or item.get("showTitle"),
        "season_number": _as_int(item.get("parentIndex")),
        "episode_number": _as_int(item.get("index")),
        "summary": item.get("summary"),
        "content_rating": item.get("contentRating"),
        "audience_rating": _as_float(item.get("audienceRating")),
        "user_rating": _as_float(item.get("userRating")),
        "originally_available_at": item.get("originallyAvailableAt"),
        "added_at": _plex_time(item.get("addedAt")),
        "updated_at": _plex_time(item.get("updatedAt")),
        "last_viewed_at": _plex_time(item.get("lastViewedAt")),
        "view_count": _as_int(item.get("viewCount")) or 0,
        "thumb": item.get("thumb"),
        "art": item.get("art"),
        "collections": _tag_values(item, "Collection"),
        "genres": _tag_values(item, "Genre"),
        "labels": _tag_values(item, "Label"),
    }


def _parts_from_item(item: dict[str, Any]) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for media in _as_list(item.get("Media")):
        for part in _as_list(media.get("Part")):
            file_path = part.get("file")
            if not file_path:
                continue
            parts.append(
                {
                    "part_id": str(part.get("id") or ""),
                    "file_path": str(file_path),
                    "size_bytes": _as_int(part.get("size")),
                    "duration_ms": _as_int(part.get("duration")) or _as_int(media.get("duration")),
                    "container": part.get("container") or media.get("container"),
                }
            )
    return parts


def _tag_values(item: dict[str, Any], key: str) -> list[str]:
    values = []
    for entry in _as_list(item.get(key)):
        value = entry.get("tag") or entry.get("title") or entry.get("label")
        if value:
            values.append(str(value))
    return values


def _clean_mappings(value: Any) -> list[dict[str, str]]:
    mappings = []
    for item in _as_list(value):
        plex_prefix = normalize_path(item.get("plex_path_prefix") or item.get("plex") or "")
        media_prefix = normalize_path(item.get("media_atlas_path_prefix") or item.get("media_atlas") or "")
        if plex_prefix and media_prefix:
            mappings.append({"plex_path_prefix": plex_prefix, "media_atlas_path_prefix": media_prefix})
    return mappings


def _sync_canceled(job_id: int) -> bool:
    row = db.query_one("SELECT cancel_requested FROM plex_sync_jobs WHERE id = ?", (job_id,))
    return bool(row and row.get("cancel_requested"))


def _finish_sync(job_id: int, status: str, message: str, error: str | None) -> None:
    db.execute(
        """
        UPDATE plex_sync_jobs
        SET status = ?, finished_at = ?, message = ?, error_message = ?
        WHERE id = ?
        """,
        (status, db.utc_now(), message, error, job_id),
    )


def _media_container(data: dict[str, Any]) -> dict[str, Any]:
    container = data.get("MediaContainer") if isinstance(data, dict) else None
    if isinstance(container, dict):
        return container
    return data


def _xml_to_dict(text: str) -> dict[str, Any]:
    root = ET.fromstring(text)

    def convert(node: ET.Element) -> dict[str, Any]:
        payload: dict[str, Any] = dict(node.attrib)
        for child in node:
            payload.setdefault(child.tag, []).append(convert(child))
        return payload

    return {root.tag: convert(root)}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _plex_time(value: Any) -> str | None:
    number = _as_int(value)
    if number is None:
        return str(value) if value else None
    return datetime.fromtimestamp(number, UTC).isoformat(timespec="seconds")
