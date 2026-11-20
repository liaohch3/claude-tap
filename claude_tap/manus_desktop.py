"""Read Manus Studio's open task through a local macOS Accessibility helper."""

from __future__ import annotations

import asyncio
import json
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any


class ManusDesktopError(RuntimeError):
    """An Accessibility helper error safe to show without task contents."""


async def _run_helper(*command: str, timeout: float, failure: str) -> bytes:
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        raise ManusDesktopError(failure) from None
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except (TimeoutError, asyncio.CancelledError) as exc:
        with suppress(ProcessLookupError):
            process.kill()
        await process.communicate()
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise ManusDesktopError(failure) from None
    if process.returncode:
        raise ManusDesktopError(failure)
    return output


class ManusDesktopReader:
    """Obtain a snapshot of the task open in Manus Studio's active window."""

    def __init__(self, executable: Path) -> None:
        self._executable = executable

    async def read(self) -> dict[str, Any]:
        failure = "Could not read the Manus Studio Accessibility snapshot."
        output = await _run_helper(str(self._executable), timeout=10, failure=failure)
        try:
            snapshot = json.loads(output)
        except (ValueError, RecursionError):
            raise ManusDesktopError(failure) from None
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("status"), str):
            raise ManusDesktopError(failure)
        return snapshot


@asynccontextmanager
async def manus_desktop_reader() -> AsyncIterator[ManusDesktopReader]:
    """Build the bundled helper once for the lifetime of this watcher."""
    source = Path(__file__).with_name("manus_accessibility.swift")
    with tempfile.TemporaryDirectory(prefix="claude-tap-manus-") as temp_dir:
        executable = Path(temp_dir) / "manus-accessibility"
        await _run_helper(
            "/usr/bin/swiftc",
            "-O",
            str(source),
            "-o",
            str(executable),
            timeout=90,
            failure="Could not build the Manus Accessibility helper. Install the macOS Command Line Tools with xcode-select --install.",
        )
        yield ManusDesktopReader(executable)
