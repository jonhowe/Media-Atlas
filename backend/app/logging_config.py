from __future__ import annotations

import json
import logging
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any


APPLICATION_LOG_DIRECTORY = "application"
APPLICATION_LOG_FILENAME = "media-atlas.jsonl"
APPLICATION_LOG_SCAN_LIMIT = 10_000


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("request_id", "method", "path", "status_code", "duration_ms", "job_id", "run_id"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging() -> None:
    root = logging.getLogger()
    if getattr(configure_logging, "_configured", False):
        return
    from .config import CONFIG

    formatter = JsonFormatter()
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    path = application_log_path(CONFIG.logs_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = TimedRotatingFileHandler(
        path,
        when="midnight",
        interval=1,
        backupCount=0,
        encoding="utf-8",
        delay=True,
        utc=True,
    )
    file_handler.setFormatter(formatter)

    root.handlers = [console_handler, file_handler]
    root.setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").disabled = True
    setattr(configure_logging, "_configured", True)


def application_log_path(logs_dir: Path) -> Path:
    return logs_dir / APPLICATION_LOG_DIRECTORY / APPLICATION_LOG_FILENAME


def read_application_logs(
    logs_dir: Path,
    *,
    limit: int = 200,
    level: str | None = None,
    logger_prefix: str | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    """Read the newest matching structured application log entries safely."""
    log_dir = application_log_path(logs_dir).parent
    if not log_dir.exists():
        return {"items": [], "limit": limit, "truncated": False}

    paths = []
    for path in log_dir.glob(f"{APPLICATION_LOG_FILENAME}*"):
        try:
            if (
                (path.name == APPLICATION_LOG_FILENAME or path.name.startswith(f"{APPLICATION_LOG_FILENAME}."))
                and path.is_file()
                and not path.is_symlink()
            ):
                paths.append(path)
        except OSError:
            continue
    paths.sort(key=_log_path_sort_key, reverse=True)
    normalized_level = level.casefold() if level else None
    normalized_logger = logger_prefix.casefold() if logger_prefix else None
    normalized_query = query.casefold() if query else None
    newest_first: list[dict[str, Any]] = []
    scanned_lines = 0

    for path in paths:
        try:
            for raw_line in _iter_lines_reverse(path):
                if scanned_lines >= APPLICATION_LOG_SCAN_LIMIT:
                    return {
                        "items": list(reversed(newest_first[:limit])),
                        "limit": limit,
                        "truncated": True,
                    }
                scanned_lines += 1
                try:
                    item = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(item, dict):
                    continue
                if not all(key in item for key in ("timestamp", "level", "logger", "message")):
                    continue
                if normalized_level and str(item["level"]).casefold() != normalized_level:
                    continue
                if normalized_logger and not str(item["logger"]).casefold().startswith(normalized_logger):
                    continue
                if normalized_query:
                    searchable = json.dumps(item, ensure_ascii=False, default=str).casefold()
                    if normalized_query not in searchable:
                        continue
                newest_first.append(item)
                if len(newest_first) > limit:
                    return {
                        "items": list(reversed(newest_first[:limit])),
                        "limit": limit,
                        "truncated": True,
                    }
        except OSError:
            # A rotation or retention pass can remove a file between listing and reading it.
            continue

    return {
        "items": list(reversed(newest_first)),
        "limit": limit,
        "truncated": False,
    }


def _log_path_sort_key(path: Path) -> tuple[int, str]:
    try:
        return path.stat().st_mtime_ns, path.name
    except OSError:
        return 0, path.name


def _iter_lines_reverse(path: Path, block_size: int = 64 * 1024) -> Iterator[str]:
    """Yield non-empty binary lines from a file, newest first."""
    with path.open("rb") as handle:
        handle.seek(0, 2)
        position = handle.tell()
        buffer = b""
        while position > 0:
            read_size = min(block_size, position)
            position -= read_size
            handle.seek(position)
            buffer = handle.read(read_size) + buffer
            lines = buffer.split(b"\n")
            buffer = lines[0]
            for line in reversed(lines[1:]):
                if line:
                    yield line.decode("utf-8", "replace")
        if buffer:
            yield buffer.decode("utf-8", "replace")
