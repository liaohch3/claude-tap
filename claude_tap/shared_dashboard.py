"""Shared local dashboard process used by concurrent claude-tap sessions."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import aiohttp

from claude_tap.trace_store import resolve_db_path

DEFAULT_DASHBOARD_PORT = 19527
_DASHBOARD_HEALTH_TIMEOUT = 1.5
_DASHBOARD_SESSIONS_HEALTH_TIMEOUT = 3.0
_DASHBOARD_LOCK_NAME = "dashboard.lock"


def resolve_dashboard_port(explicit: int | None = None) -> int:
    """Return the shared dashboard port (fixed default unless overridden)."""
    if explicit is not None and explicit > 0:
        return explicit
    override = os.environ.get("CLOUDTAP_DASHBOARD_PORT", "").strip()
    if override.isdigit():
        return int(override)
    return DEFAULT_DASHBOARD_PORT


def dashboard_url(host: str, port: int) -> str:
    return f"http://{host}:{port}"


def _dashboard_lock_path() -> Path:
    return resolve_db_path().parent / _DASHBOARD_LOCK_NAME


@contextmanager
def _dashboard_spawn_lock():
    path = _dashboard_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as lock_file:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


async def _dashboard_get_status(url: str, *, timeout_seconds: float) -> int | None:
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                return resp.status
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
        return None


async def is_dashboard_healthy(host: str, port: int) -> bool:
    """Return True when the shared dashboard responds to a cheap health check."""
    base_url = dashboard_url(host, port)
    status = await _dashboard_get_status(
        f"{base_url}/dashboard/health",
        timeout_seconds=_DASHBOARD_HEALTH_TIMEOUT,
    )
    if status == 200:
        return True
    if status not in {404, 405}:
        return False

    fallback_status = await _dashboard_get_status(
        f"{base_url}/api/sessions",
        timeout_seconds=_DASHBOARD_SESSIONS_HEALTH_TIMEOUT,
    )
    return fallback_status == 200


async def wait_for_dashboard_healthy(
    host: str,
    port: int,
    *,
    timeout: float = 8.0,
    interval: float = 0.1,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await is_dashboard_healthy(host, port):
            return True
        await asyncio.sleep(interval)
    return False


def _spawn_dashboard_subprocess(host: str, port: int, output_dir: Path) -> subprocess.Popen[bytes]:
    cmd = [
        sys.executable,
        "-m",
        "claude_tap",
        "dashboard",
        "--tap-live-port",
        str(port),
        "--tap-no-open",
        "--tap-output-dir",
        str(output_dir),
    ]
    if host and host != "127.0.0.1":
        cmd.extend(["--tap-host", host])

    kwargs: dict = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs)


async def ensure_shared_dashboard(
    *,
    host: str,
    port: int,
    output_dir: Path,
    open_browser: bool,
    open_browser_fn,
) -> tuple[str, bool]:
    """Ensure the shared dashboard is running; return (url, spawned_by_caller)."""
    url = dashboard_url(host, port)
    if await is_dashboard_healthy(host, port):
        return url, False

    with _dashboard_spawn_lock():
        if await is_dashboard_healthy(host, port):
            return url, False
        _spawn_dashboard_subprocess(host, port, output_dir)
        if not await wait_for_dashboard_healthy(host, port):
            raise RuntimeError(f"Failed to start shared dashboard on {url}")

    if open_browser:
        open_browser_fn(url)
    return url, True
