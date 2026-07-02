from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Iterator

from ..db import loads_json


def _normalize_extensions(raw_json: str) -> set[str]:
    values = loads_json(raw_json, [])
    normalized = set()
    for value in values:
        item = str(value).lower().strip()
        if item and not item.startswith("."):
            item = f".{item}"
        if item:
            normalized.add(item)
    return normalized


def _matches_any(path: Path, root: Path, patterns: list[str]) -> bool:
    rel = path.relative_to(root).as_posix()
    name = path.name
    for pattern in patterns:
        lowered = pattern.lower()
        if fnmatch.fnmatch(rel.lower(), lowered) or fnmatch.fnmatch(name.lower(), lowered):
            return True
    return False


def discover_media_files(root: dict) -> Iterator[Path]:
    root_path = Path(root["path"]).expanduser().resolve()
    extensions = _normalize_extensions(root["include_extensions_json"])
    excludes = loads_json(root["exclude_patterns_json"], [])

    for current, dirnames, filenames in os.walk(root_path):
        current_path = Path(current)
        dirnames[:] = [
            dirname
            for dirname in dirnames
            if not _matches_any(current_path / dirname, root_path, excludes)
        ]
        for filename in filenames:
            file_path = current_path / filename
            if file_path.suffix.lower() not in extensions:
                continue
            if _matches_any(file_path, root_path, excludes):
                continue
            yield file_path
