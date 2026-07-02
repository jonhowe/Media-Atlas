from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from ..config import CONFIG


class ProbeError(Exception):
    def __init__(self, message: str, exit_code: int | None = None, stderr: str | None = None):
        super().__init__(message)
        self.exit_code = exit_code
        self.stderr = stderr or message


async def probe_file(path: str | Path, timeout_seconds: int | None = None) -> dict[str, Any]:
    args = [
        CONFIG.scanner.ffprobe_path,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        "-show_chapters",
        str(path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    timeout = timeout_seconds or CONFIG.scanner.timeout_seconds
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise ProbeError(f"ffprobe timed out after {timeout} seconds", stderr=str(exc)) from exc

    if proc.returncode != 0:
        stderr_text = stderr.decode("utf-8", "replace")
        raise ProbeError(
            stderr_text or f"ffprobe exited with {proc.returncode}",
            exit_code=proc.returncode,
            stderr=stderr_text,
        )

    try:
        return json.loads(stdout.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ProbeError(f"ffprobe returned invalid JSON: {exc}") from exc
