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
    _sync_dashboard_healthy_for_current_db,
    dashboard_connect_host,
    dashboard_url,
    ensure_shared_dashboard,
    is_dashboard_healthy,
    is_legacy_dashboard_healthy,
    resolve_dashboard_port,
    stop_shared_dashboard,
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


@pytest.mark.parametrize("value", ["0", "-1", "not-a-port"])
def test_resolve_dashboard_port_ignores_invalid_env(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("CLOUDTAP_DASHBOARD_PORT", value)
    assert resolve_dashboard_port(0) == DEFAULT_DASHBOARD_PORT


def test_dashboard_url() -> None:
    assert dashboard_connect_host("localhost") == "localhost"
    assert dashboard_connect_host(" ") == "127.0.0.1"
    assert dashboard_connect_host("0.0.0.0") == "127.0.0.1"
    assert dashboard_connect_host("::") == "::1"
    assert dashboard_connect_host("[::]") == "::1"
    assert dashboard_url("127.0.0.1", 1234) == "http://127.0.0.1:1234"
    assert dashboard_url("0.0.0.0", 1234) == "http://127.0.0.1:1234"
    assert dashboard_url("::", 1234) == "http://[::1]:1234"
    assert dashboard_url("::1", 1234) == "http://[::1]:1234"
    assert dashboard_url("[::1]", 1234) == "http://[::1]:1234"


def test_sync_dashboard_health_uses_proxyless_opener(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class FakeResponse:
        status = 200

        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return json_bytes

    class FakeOpener:
        def open(self, url: str, *, timeout: float) -> FakeResponse:
            calls.append((url, timeout))
            return FakeResponse()

    db_path = tmp_path / "health.sqlite3"
    json_bytes = f'{{"ok":true,"db_path":"{db_path}"}}'.encode()
    calls: list[tuple[str, float]] = []
    monkeypatch.setenv("CLOUDTAP_DB", str(db_path))
    monkeypatch.setattr("claude_tap.shared_dashboard._LOCAL_DASHBOARD_OPENER", FakeOpener())

    assert _sync_dashboard_healthy_for_current_db("127.0.0.1", 19527) is True
    assert calls and calls[0][0] == "http://127.0.0.1:19527/dashboard/health"


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
    spawned_args: list[tuple[list[str], dict[str, object]]] = []

    class FakePopen:
        def __init__(self, cmd: list[str], **kwargs: object) -> None:
            spawned_args.append((cmd, kwargs))
            self.pid = 99999

    monkeypatch.setattr(subprocess, "Popen", FakePopen)

    _spawn_dashboard_subprocess("127.0.0.1", 19527, tmp_path)

    assert len(spawned_args) == 1
    cmd, kwargs = spawned_args[0]
    assert "dashboard" in cmd
    assert "--tap-live-port" in cmd
    assert "19527" in cmd
    assert str(tmp_path) in cmd
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stdout"] == subprocess.DEVNULL
    assert kwargs["stderr"] == subprocess.DEVNULL
    assert kwargs["start_new_session"] is True


def test_spawn_dashboard_subprocess_hides_windows_console(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    class FakeStartupInfo:
        def __init__(self) -> None:
            self.dwFlags = 0
            self.wShowWindow: int | None = None

    class FakePopen:
        def __init__(self, cmd: list[str], **kwargs: object) -> None:
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            self.pid = 99999

    scripts_dir = tmp_path / "uv" / "tools" / "claude-tap" / "Scripts"
    scripts_dir.mkdir(parents=True)
    python_exe = scripts_dir / "python.exe"
    pythonw_exe = scripts_dir / "pythonw.exe"
    python_exe.touch()
    pythonw_exe.touch()

    monkeypatch.setattr("claude_tap.shared_dashboard.sys.platform", "win32")
    monkeypatch.setattr("claude_tap.shared_dashboard.sys.executable", str(python_exe))
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x1000, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x2000, raising=False)
    monkeypatch.setattr(subprocess, "STARTF_USESHOWWINDOW", 0x4000, raising=False)
    monkeypatch.setattr(subprocess, "SW_HIDE", 0, raising=False)
    monkeypatch.setattr(subprocess, "STARTUPINFO", FakeStartupInfo, raising=False)

    _spawn_dashboard_subprocess("0.0.0.0", 19527, tmp_path)

    cmd = captured["cmd"]
    kwargs = captured["kwargs"]
    assert isinstance(cmd, list)
    assert isinstance(kwargs, dict)
    assert cmd[0] == str(pythonw_exe)
    assert cmd[-2:] == ["--tap-host", "0.0.0.0"]
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stdout"] == subprocess.DEVNULL
    assert kwargs["stderr"] == subprocess.DEVNULL
    assert kwargs["creationflags"] == 0x1000 | 0x2000
    assert "start_new_session" not in kwargs
    startupinfo = kwargs["startupinfo"]
    assert isinstance(startupinfo, FakeStartupInfo)
    assert startupinfo.dwFlags == 0x4000
    assert startupinfo.wShowWindow == 0


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
                payload = await resp.json()
                assert payload["ok"] is True
                assert payload["db_path"] == str(resolve_db_path())
                assert payload["dashboard_mode"] is True
                assert isinstance(payload["quit_token"], str)
                assert payload["quit_token"]
        assert await is_dashboard_healthy("127.0.0.1", port) is True
        assert await wait_for_dashboard_healthy("127.0.0.1", port, timeout=1.0) is True
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_stop_shared_dashboard_stops_real_server(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from claude_tap.live import LiveViewerServer

    monkeypatch.setenv("CLOUDTAP_DB", str(tmp_path / "dashboard.sqlite3"))

    server = LiveViewerServer(port=0, migrate_from=tmp_path, dashboard_mode=True)
    port = await server.start()
    try:
        assert await stop_shared_dashboard("127.0.0.1", port) is True
        assert await is_dashboard_healthy("127.0.0.1", port, require_current_db=False) is False
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_bind_all_dashboard_uses_loopback_for_local_controls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from claude_tap.live import LiveViewerServer

    monkeypatch.setenv("CLOUDTAP_DB", str(tmp_path / "dashboard.sqlite3"))

    server = LiveViewerServer(port=0, host="0.0.0.0", migrate_from=tmp_path, dashboard_mode=True)
    port = await server.start()
    try:
        assert server.url == f"http://127.0.0.1:{port}"
        assert await is_dashboard_healthy("0.0.0.0", port) is True
        assert await stop_shared_dashboard("0.0.0.0", port) is True
        assert await is_dashboard_healthy("0.0.0.0", port, require_current_db=False) is False
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_stop_shared_dashboard_requires_health_token() -> None:
    quit_called = False

    async def health(request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def quit_dashboard(request: web.Request) -> web.Response:
        nonlocal quit_called
        quit_called = True
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_get("/dashboard/health", health)
    app.router.add_post("/dashboard/quit", quit_dashboard)
    runner, port = await _start_test_app(app)
    try:
        assert await stop_shared_dashboard("127.0.0.1", port) is False
        assert quit_called is False
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_stop_shared_dashboard_rejects_unhealthy_or_forbidden_server() -> None:
    async def unhealthy(request: web.Request) -> web.Response:
        return web.json_response({"ok": False}, status=500)

    unhealthy_app = web.Application()
    unhealthy_app.router.add_get("/dashboard/health", unhealthy)
    unhealthy_runner, unhealthy_port = await _start_test_app(unhealthy_app)
    try:
        assert await stop_shared_dashboard("127.0.0.1", unhealthy_port) is False
    finally:
        await unhealthy_runner.cleanup()

    async def health(request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "quit_token": "test-token"})

    async def forbidden_quit(request: web.Request) -> web.Response:
        assert request.headers["X-Claude-Tap-Dashboard-Token"] == "test-token"
        return web.json_response({"ok": False}, status=403)

    forbidden_app = web.Application()
    forbidden_app.router.add_get("/dashboard/health", health)
    forbidden_app.router.add_post("/dashboard/quit", forbidden_quit)
    forbidden_runner, forbidden_port = await _start_test_app(forbidden_app)
    try:
        assert await stop_shared_dashboard("127.0.0.1", forbidden_port) is False
    finally:
        await forbidden_runner.cleanup()


@pytest.mark.asyncio
async def test_stop_shared_dashboard_handles_post_client_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import aiohttp

    async def healthy(*_args: object, **_kwargs: object) -> tuple[int, dict[str, str]]:
        return 200, {"quit_token": "test-token"}

    class FailingSession:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "FailingSession":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        def post(self, *_args: object, **_kwargs: object) -> object:
            raise aiohttp.ClientError("post failed")

    monkeypatch.setattr("claude_tap.shared_dashboard._dashboard_get_status_and_payload", healthy)
    monkeypatch.setattr("claude_tap.shared_dashboard.aiohttp.ClientSession", FailingSession)

    assert await stop_shared_dashboard("127.0.0.1", 19527) is False


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
        assert await is_legacy_dashboard_healthy("127.0.0.1", port) is False
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
        assert await is_legacy_dashboard_healthy("127.0.0.1", port) is True
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_ensure_shared_dashboard_already_healthy_does_not_reopen_browser(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def mock_true(h: str, p: int) -> bool:
        return True

    migrated: list[Path] = []
    monkeypatch.setattr("claude_tap.shared_dashboard.is_dashboard_healthy", mock_true)
    monkeypatch.setattr("claude_tap.history.migrate_legacy_traces", migrated.append)

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
    assert opened == []
    assert migrated == [tmp_path]


@pytest.mark.asyncio
async def test_ensure_shared_dashboard_migrates_after_lock_time_reuse(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def mock_false(h: str, p: int) -> bool:
        return False

    migrated: list[Path] = []
    monkeypatch.setattr("claude_tap.shared_dashboard.is_dashboard_healthy", mock_false)
    monkeypatch.setattr("claude_tap.shared_dashboard.is_legacy_dashboard_healthy", mock_false)
    monkeypatch.setattr("claude_tap.shared_dashboard._spawn_dashboard_subprocess_if_needed", lambda h, p, d: False)
    monkeypatch.setattr("claude_tap.shared_dashboard._migrate_legacy_traces", migrated.append)

    opened: list[str] = []

    url, spawned = await ensure_shared_dashboard(
        host="127.0.0.1",
        port=19527,
        output_dir=tmp_path,
        open_browser=True,
        open_browser_fn=opened.append,
    )

    assert url == "http://127.0.0.1:19527"
    assert spawned is False
    assert opened == []
    assert migrated == [tmp_path]


@pytest.mark.asyncio
async def test_ensure_shared_dashboard_spawns(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CLOUDTAP_DB", str(tmp_path / "test.sqlite3"))

    health_calls: list[int] = []

    async def mock_health(h: str, p: int) -> bool:
        if len(health_calls) < 2:
            health_calls.append(1)
            return False
        return True

    async def mock_legacy_false(h: str, p: int) -> bool:
        return False

    monkeypatch.setattr("claude_tap.shared_dashboard.is_dashboard_healthy", mock_health)
    monkeypatch.setattr("claude_tap.shared_dashboard.is_legacy_dashboard_healthy", mock_legacy_false)
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
    monkeypatch.setattr("claude_tap.shared_dashboard.is_legacy_dashboard_healthy", mock_false)
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
