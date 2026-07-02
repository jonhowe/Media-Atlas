from __future__ import annotations

from pathlib import Path

from ..config import CONFIG


def resolve_existing_directory(path: str) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        raise ValueError("Path does not exist.")
    if not resolved.is_dir():
        raise ValueError("Path is not a directory.")
    return resolved


def is_within_allowed_browse_roots(path: Path) -> bool:
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        return False
    for root in CONFIG.allowed_browse_roots:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def safe_shell_join(args: list[str]) -> str:
    import shlex

    return " ".join(shlex.quote(arg) for arg in args)
