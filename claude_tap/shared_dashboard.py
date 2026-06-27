"""Shared local dashboard process used by concurrent claude-tap sessions."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from importlib.metadata import version as _pkg_version
from pathlib import Path

import aiohttp

from claude_tap.process_utils import windows_no_console_subprocess_kwargs
from claude_tap.trace_store import resolve_db_path

DEFAULT_DASHBOARD_PORT = 19527
_DASHBOARD_HEALTH_TIMEOUT = 1.5
_DASHBOARD_SESSIONS_HEALTH_TIMEOUT = 3.0
_DASHBOARD_QUIT_TIMEOUT = 2.0
_DASHBOARD_LOCK_NAME = "dashboard.lock"
_DASHBOARD_QUIT_TOKEN_HEADER = "X-Claude-Tap-Dashboard-Token"

try:
    CLAUDE_TAP_VERSION = _pkg_version("claude-tap")
except Exception:
    CLAUDE_TAP_VERSION = "0.0.0"


def resolve_dashboard_port(explicit: int | None = None) -> int:
    """Return the shared dashboard port (fixed default unless overridden)."""
    if explicit is not None and explicit > 0:
        return explicit
    override = os.environ.get("CLOUDTAP_DASHBOARD_PORT", "").strip()
    if override.isdigit() and int(override) > 0:
        return int(override)
    return DEFAULT_DASHBOARD_PORT


def dashboard_connect_host(host: str) -> str:
    """Return the local address clients should use for a dashboard bind host."""
    normalized_host = host.strip()
    bare_host = normalized_host.strip("[]")
    try:
        address = ipaddress.ip_address(bare_host)
    except ValueError:
        return normalized_host or "127.0.0.1"
    if address.is_unspecified:
        return "::1" if address.version == 6 else "127.0.0.1"
    return bare_host


def dashboard_url(host: str, port: int) -> str:
    normalized_host = dashboard_connect_host(host)
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


def _dashboard_health_matches_current_instance(payload: dict | None) -> bool:
    return bool(
        payload
        and payload.get("ok") is True
        and payload.get("db_path") == str(resolve_db_path())
        and payload.get("version") == CLAUDE_TAP_VERSION
    )


def _sync_dashboard_healthy_for_current_db(host: str, port: int) -> bool:
    url = f"{dashboard_url(host, port)}/dashboard/health"
    try:
        with _LOCAL_DASHBOARD_OPENER.open(url, timeout=_DASHBOARD_HEALTH_TIMEOUT) as resp:
            if resp.status != 200:
                return False
            payload = json.loads(resp.read().decode("utf-8"))
    except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    return _dashboard_health_matches_current_instance(payload if isinstance(payload, dict) else None)


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
        return not require_current_db or _dashboard_health_matches_current_instance(payload)
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


async def stop_shared_dashboard(host: str, port: int) -> bool:
    """Ask a running shared dashboard service to stop and wait until it is gone."""
    base_url = dashboard_url(host, port)
    timeout = aiohttp.ClientTimeout(total=_DASHBOARD_QUIT_TIMEOUT)
    status, payload = await _dashboard_get_status_and_payload(
        f"{base_url}/dashboard/health",
        timeout_seconds=_DASHBOARD_HEALTH_TIMEOUT,
    )
    if status != 200 or not isinstance(payload, dict):
        return False
    quit_token = payload.get("quit_token")
    if not isinstance(quit_token, str) or not quit_token:
        return False

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{base_url}/dashboard/quit",
                headers={_DASHBOARD_QUIT_TOKEN_HEADER: quit_token},
            ) as resp:
                if resp.status != 200:
                    return False
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
        return False

    return await wait_for_dashboard_stopped(host, port)


async def stop_dashboard_service(host: str, port: int) -> bool:
    """Stop a current dashboard, including older dashboards without a quit endpoint."""
    if await stop_shared_dashboard(host, port):
        return True
    if not await is_legacy_dashboard_healthy(host, port):
        return False
    return await stop_legacy_dashboard_process(host, port)


async def stop_legacy_dashboard_process(host: str, port: int) -> bool:
    pids = await asyncio.to_thread(_dashboard_listening_pids_for_port, port)
    if not pids:
        return False
    terminated = await asyncio.to_thread(_terminate_legacy_dashboard_pids, pids, port)
    return terminated and await wait_for_dashboard_stopped(host, port)


def _dashboard_listening_pids_for_port(port: int) -> list[int]:
    if port <= 0:
        return []
    pids: set[int] = set()
    lsof = shutil.which("lsof")
    if lsof:
        try:
            result = subprocess.run(
                [lsof, "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                check=False,
                capture_output=True,
                text=True,
                timeout=2.0,
            )
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if result and result.returncode in {0, 1}:
            for line in result.stdout.splitlines():
                if line.strip().isdigit():
                    pids.add(int(line.strip()))

    if pids:
        return sorted(pids)

    ss = shutil.which("ss")
    if ss:
        try:
            result = subprocess.run(
                [ss, "-ltnp", f"sport = :{port}"],
                check=False,
                capture_output=True,
                text=True,
                timeout=2.0,
            )
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if result and result.returncode == 0:
            for match in re.finditer(r"pid=(\d+)", result.stdout):
                pids.add(int(match.group(1)))

    return sorted(pids)


def _dashboard_process_command(pid: int) -> str:
    if pid <= 0:
        return ""
    if sys.platform.startswith("linux"):
        try:
            parts = [
                part.decode("utf-8", errors="replace")
                for part in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
                if part
            ]
        except OSError:
            parts = []
        if parts:
            return " ".join(parts)

    ps = shutil.which("ps")
    if not ps:
        return ""
    try:
        result = subprocess.run(
            [ps, "-p", str(pid), "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _looks_like_legacy_dashboard_command(command: str, port: int) -> bool:
    normalized = " ".join(command.split())
    lower = normalized.lower()
    if "claude_tap" not in lower and "claude-tap" not in lower:
        return False
    if not re.search(r"(^|\s)dashboard($|\s)", lower):
        return False
    if "--tap-live-port" in lower and str(port) not in lower:
        return False
    return True


def _terminate_legacy_dashboard_pids(pids: list[int], port: int) -> bool:
    terminated = False
    for pid in pids:
        if pid == os.getpid():
            continue
        command = _dashboard_process_command(pid)
        if not _looks_like_legacy_dashboard_command(command, port):
            continue
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            continue
        terminated = True
    return terminated


async def stop_incompatible_dashboard_if_running(host: str, port: int, url: str) -> None:
    """Stop a dashboard that is listening on the shared port but cannot be reused.

    The common case is an older claude-tap dashboard left running after the CLI
    was upgraded. Reusing it would keep serving the old packaged HTML/JS.
    """
    if not await is_dashboard_healthy(host, port, require_current_db=False):
        return
    if await stop_dashboard_service(host, port):
        return
    raise RuntimeError(
        "A different or outdated claude-tap dashboard is already running on "
        f"{url}. Stop it first with `claude-tap dashboard stop --tap-live-port {port}`. "
        "If that fails, terminate the old dashboard process manually."
    )


async def wait_for_dashboard_stopped(
    host: str,
    port: int,
    *,
    timeout: float = 5.0,
    interval: float = 0.1,
) -> bool:
    """Return True once no dashboard responds on the configured endpoint."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not await is_dashboard_healthy(host, port, require_current_db=False):
            return True
        await asyncio.sleep(interval)
    return False


def _spawn_dashboard_subprocess(host: str, port: int, output_dir: Path) -> subprocess.Popen[bytes]:
    cmd = [
        _dashboard_python_executable(),
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
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        kwargs.update(windows_no_console_subprocess_kwargs())
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs)


def _dashboard_python_executable() -> str:
    if sys.platform != "win32":
        return sys.executable

    executable = Path(sys.executable)
    if executable.name.lower() != "python.exe":
        return sys.executable

    pythonw = executable.with_name("pythonw.exe")
    if pythonw.exists():
        return str(pythonw)
    return sys.executable


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
        _migrate_legacy_traces(output_dir)
        return url, False

    await stop_incompatible_dashboard_if_running(host, port, url)

    spawned = await asyncio.to_thread(_spawn_dashboard_subprocess_if_needed, host, port, output_dir)
    if spawned:
        if not await wait_for_dashboard_healthy(host, port):
            raise RuntimeError(f"Failed to start shared dashboard on {url}")
    else:
        _migrate_legacy_traces(output_dir)

    if open_browser and spawned:
        open_browser_fn(url)
    return url, spawned
