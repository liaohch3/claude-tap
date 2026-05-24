from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from aiohttp import web

from claude_tap.shared_dashboard import (
    DEFAULT_DASHBOARD_PORT,
    _dashboard_lock_path,
    _dashboard_spawn_lock,
    _spawn_dashboard_subprocess,
    dashboard_url,
    ensure_shared_dashboard,
    is_dashboard_healthy,
    resolve_dashboard_port,
)
from claude_tap.trace_store import resolve_db_path


async def _start_test_app(app: web.Application) -> tuple[web.AppRunner, int]:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, port


def test_resolve_dashboard_port_defaults_to_shared_port() -> None:
    assert resolve_dashboard_port(0) == DEFAULT_DASHBOARD_PORT
    assert resolve_dashboard_port(None) == DEFAULT_DASHBOARD_PORT


def test_resolve_dashboard_port_honors_explicit_port() -> None:
    assert resolve_dashboard_port(3000) == 3000


def test_resolve_dashboard_port_honors_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOUDTAP_DASHBOARD_PORT", "8765")
    assert resolve_dashboard_port(0) == 8765


def test_dashboard_url() -> None:
    assert dashboard_url("127.0.0.1", 1234) == "http://127.0.0.1:1234"


def test_dashboard_lock_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CLOUDTAP_DB", str(tmp_path / "test.sqlite3"))
    assert _dashboard_lock_path() == tmp_path / "dashboard.lock"


def test_dashboard_spawn_lock(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CLOUDTAP_DB", str(tmp_path / "test.sqlite3"))
    with _dashboard_spawn_lock():
        pass
    with _dashboard_spawn_lock():
        pass


def test_spawn_dashboard_subprocess(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    spawned_args = []

    class FakePopen:
        def __init__(self, cmd: list[str], **kwargs: object) -> None:
            spawned_args.append(cmd)
            self.pid = 99999

    monkeypatch.setattr(subprocess, "Popen", FakePopen)

    _spawn_dashboard_subprocess("127.0.0.1", 19527, tmp_path)

    assert len(spawned_args) == 1
    cmd = spawned_args[0]
    assert "dashboard" in cmd
    assert "--tap-live-port" in cmd
    assert "19527" in cmd
    assert str(tmp_path) in cmd


@pytest.mark.asyncio
async def test_is_dashboard_healthy_real_server(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import aiohttp

    from claude_tap.live import LiveViewerServer
    from claude_tap.shared_dashboard import wait_for_dashboard_healthy

    monkeypatch.setenv("CLOUDTAP_DB", str(tmp_path / "dashboard.sqlite3"))

    # Before starting, it should be unhealthy
    assert await is_dashboard_healthy("127.0.0.1", 54321) is False
    assert await wait_for_dashboard_healthy("127.0.0.1", 54321, timeout=0.2, interval=0.05) is False

    # Start real server
    server = LiveViewerServer(port=0, migrate_from=tmp_path, dashboard_mode=True)
    port = await server.start()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/dashboard/health") as resp:
                assert resp.status == 200
                assert await resp.json() == {"ok": True, "db_path": str(resolve_db_path())}
        assert await is_dashboard_healthy("127.0.0.1", port) is True
        assert await wait_for_dashboard_healthy("127.0.0.1", port, timeout=1.0) is True
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_is_dashboard_healthy_prefers_lightweight_health_route(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CLOUDTAP_DB", str(tmp_path / "health.sqlite3"))
    sessions_seen = False
    app = web.Application()

    async def health(request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "db_path": str(resolve_db_path())})

    async def sessions(request: web.Request) -> web.Response:
        nonlocal sessions_seen
        sessions_seen = True
        return web.json_response({"sessions": []})

    app.router.add_get("/dashboard/health", health)
    app.router.add_get("/api/sessions", sessions)
    runner, port = await _start_test_app(app)
    try:
        assert await is_dashboard_healthy("127.0.0.1", port) is True
        assert sessions_seen is False
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_is_dashboard_healthy_rejects_different_database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CLOUDTAP_DB", str(tmp_path / "current.sqlite3"))
    app = web.Application()

    async def health(request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "db_path": str(tmp_path / "other.sqlite3")})

    app.router.add_get("/dashboard/health", health)
    runner, port = await _start_test_app(app)
    try:
        assert await is_dashboard_healthy("127.0.0.1", port) is False
        assert await is_dashboard_healthy("127.0.0.1", port, require_current_db=False) is True
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_is_dashboard_healthy_falls_back_for_legacy_dashboard() -> None:
    app = web.Application()

    async def sessions(request: web.Request) -> web.Response:
        return web.json_response({"sessions": []})

    app.router.add_get("/api/sessions", sessions)
    runner, port = await _start_test_app(app)
    try:
        assert await is_dashboard_healthy("127.0.0.1", port) is False
        assert await is_dashboard_healthy("127.0.0.1", port, require_current_db=False) is True
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_ensure_shared_dashboard_already_healthy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def mock_true(h: str, p: int) -> bool:
        return True

    monkeypatch.setattr("claude_tap.shared_dashboard.is_dashboard_healthy", mock_true)

    opened = []

    def fake_open(url: str) -> None:
        opened.append(url)

    url, spawned = await ensure_shared_dashboard(
        host="127.0.0.1",
        port=19527,
        output_dir=tmp_path,
        open_browser=True,
        open_browser_fn=fake_open,
    )

    assert url == "http://127.0.0.1:19527"
    assert spawned is False
    assert opened == ["http://127.0.0.1:19527"]


@pytest.mark.asyncio
async def test_ensure_shared_dashboard_spawns(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CLOUDTAP_DB", str(tmp_path / "test.sqlite3"))

    health_calls: list[int] = []

    async def mock_health(h: str, p: int) -> bool:
        if len(health_calls) < 2:
            health_calls.append(1)
            return False
        return True

    monkeypatch.setattr("claude_tap.shared_dashboard.is_dashboard_healthy", mock_health)
    monkeypatch.setattr("claude_tap.shared_dashboard._spawn_dashboard_subprocess", lambda h, p, d: None)

    opened = []

    def fake_open(url: str) -> None:
        opened.append(url)

    url, spawned = await ensure_shared_dashboard(
        host="127.0.0.1",
        port=19527,
        output_dir=tmp_path,
        open_browser=True,
        open_browser_fn=fake_open,
    )

    assert url == "http://127.0.0.1:19527"
    assert spawned is True
    assert opened == ["http://127.0.0.1:19527"]


@pytest.mark.asyncio
async def test_ensure_shared_dashboard_timeout_raises_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CLOUDTAP_DB", str(tmp_path / "test.sqlite3"))

    async def mock_false(h: str, p: int) -> bool:
        return False

    async def mock_wait_false(h: str, p: int, **kw: object) -> bool:
        return False

    monkeypatch.setattr("claude_tap.shared_dashboard.is_dashboard_healthy", mock_false)
    monkeypatch.setattr("claude_tap.shared_dashboard.wait_for_dashboard_healthy", mock_wait_false)
    monkeypatch.setattr("claude_tap.shared_dashboard._spawn_dashboard_subprocess", lambda h, p, d: None)

    with pytest.raises(RuntimeError, match="Failed to start shared dashboard"):
        await ensure_shared_dashboard(
            host="127.0.0.1",
            port=19527,
            output_dir=tmp_path,
            open_browser=False,
            open_browser_fn=lambda u: None,
        )
