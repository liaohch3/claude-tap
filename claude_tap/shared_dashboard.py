"""Shared local dashboard process used by concurrent claude-tap sessions."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
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
    if override.isdigit() and int(override) > 0:
        return int(override)
    return DEFAULT_DASHBOARD_PORT


def dashboard_url(host: str, port: int) -> str:
    normalized_host = host.strip()
    if ":" in normalized_host and not normalized_host.startswith("["):
        normalized_host = f"[{normalized_host}]"
    return f"http://{normalized_host}:{port}"


_LOCAL_DASHBOARD_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


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


async def _dashboard_get_status_and_payload(
    url: str,
    *,
    timeout_seconds: float,
) -> tuple[int | None, dict | None]:
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                payload = None
                if resp.status == 200:
                    try:
                        body = await resp.json(content_type=None)
                    except (aiohttp.ClientError, json.JSONDecodeError, UnicodeDecodeError):
                        body = None
                    if isinstance(body, dict):
                        payload = body
                return resp.status, payload
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
        return None, None


def _dashboard_health_matches_current_db(payload: dict | None) -> bool:
    return bool(payload and payload.get("ok") is True and payload.get("db_path") == str(resolve_db_path()))


def _sync_dashboard_healthy_for_current_db(host: str, port: int) -> bool:
    url = f"{dashboard_url(host, port)}/dashboard/health"
    try:
        with _LOCAL_DASHBOARD_OPENER.open(url, timeout=_DASHBOARD_HEALTH_TIMEOUT) as resp:
            if resp.status != 200:
                return False
            payload = json.loads(resp.read().decode("utf-8"))
    except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    return _dashboard_health_matches_current_db(payload if isinstance(payload, dict) else None)


def _spawn_dashboard_subprocess_if_needed(host: str, port: int, output_dir: Path) -> bool:
    with _dashboard_spawn_lock():
        if _sync_dashboard_healthy_for_current_db(host, port):
            return False
        _spawn_dashboard_subprocess(host, port, output_dir)
        return True


def _migrate_legacy_traces(output_dir: Path) -> None:
    from claude_tap.history import migrate_legacy_traces

    migrate_legacy_traces(output_dir)


async def is_dashboard_healthy(host: str, port: int, *, require_current_db: bool = True) -> bool:
    """Return True when the shared dashboard responds to a cheap health check."""
    base_url = dashboard_url(host, port)
    status, payload = await _dashboard_get_status_and_payload(
        f"{base_url}/dashboard/health",
        timeout_seconds=_DASHBOARD_HEALTH_TIMEOUT,
    )
    if status == 200:
        return not require_current_db or _dashboard_health_matches_current_db(payload)
    if status not in {404, 405}:
        return False

    fallback_status = await _dashboard_get_status(
        f"{base_url}/api/sessions",
        timeout_seconds=_DASHBOARD_SESSIONS_HEALTH_TIMEOUT,
    )
    return fallback_status == 200 and not require_current_db


async def is_legacy_dashboard_healthy(host: str, port: int) -> bool:
    """Return True for pre-health-route dashboards that still serve sessions."""
    base_url = dashboard_url(host, port)
    status, _ = await _dashboard_get_status_and_payload(
        f"{base_url}/dashboard/health",
        timeout_seconds=_DASHBOARD_HEALTH_TIMEOUT,
    )
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
    if await is_dashboard_healthy(host, port) or await is_legacy_dashboard_healthy(host, port):
        _migrate_legacy_traces(output_dir)
        return url, False

    spawned = await asyncio.to_thread(_spawn_dashboard_subprocess_if_needed, host, port, output_dir)
    if spawned:
        if not await wait_for_dashboard_healthy(host, port):
            raise RuntimeError(f"Failed to start shared dashboard on {url}")
    else:
        _migrate_legacy_traces(output_dir)

    if open_browser and spawned:
        open_browser_fn(url)
    return url, spawned
