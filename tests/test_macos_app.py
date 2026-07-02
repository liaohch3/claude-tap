from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from claude_tap import macos_app
from claude_tap.cli import main_entry
from claude_tap.macos_app import (
    DashboardMonitorController,
    MacOSMenuApp,
    build_dashboard_command,
    build_proxy_command,
    parse_macos_app_args,
)


@pytest.fixture(autouse=True)
def _isolate_global_config(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect $HOME so controller tests exercising the real ``global_inject``
    callables never read or write the developer's real ~/.claude, ~/.codex, or
    ~/.claude-tap monitor state."""
    fake_home = tmp_path_factory.mktemp("home")
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.delenv("CODEX_HOME", raising=False)


def test_build_dashboard_command_starts_dashboard_without_opening_browser(tmp_path: Path) -> None:
    cmd = build_dashboard_command(
        python_executable="/usr/bin/python3",
        host="127.0.0.1",
        port=19527,
        output_dir=tmp_path,
    )

    assert cmd == [
        "/usr/bin/python3",
        "-m",
        "claude_tap",
        "dashboard",
        "--tap-live-port",
        "19527",
        "--tap-no-open",
        "--tap-output-dir",
        str(tmp_path),
    ]


def test_build_dashboard_command_adds_non_default_host(tmp_path: Path) -> None:
    cmd = build_dashboard_command(
        python_executable="/usr/bin/python3",
        host="0.0.0.0",
        port=19527,
        output_dir=tmp_path,
    )

    assert cmd[-2:] == ["--tap-host", "0.0.0.0"]


def test_build_dashboard_command_uses_frozen_executable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    cmd = build_dashboard_command(
        python_executable="/Applications/Claude Tap.app/Contents/Resources/claude-tap/claude-tap",
        host="127.0.0.1",
        port=19527,
        output_dir=tmp_path,
    )

    assert cmd[:4] == [
        "/Applications/Claude Tap.app/Contents/Resources/claude-tap/claude-tap",
        "dashboard",
        "--tap-live-port",
        "19527",
    ]


def test_build_proxy_command_starts_reverse_proxy_without_dashboard(tmp_path: Path) -> None:
    cmd = build_proxy_command(
        python_executable="/usr/bin/python3",
        client="claude",
        host="127.0.0.1",
        port=19528,
        output_dir=tmp_path,
    )

    assert cmd == [
        "/usr/bin/python3",
        "-m",
        "claude_tap",
        "--tap-no-launch",
        "--tap-client",
        "claude",
        "--tap-port",
        "19528",
        "--tap-host",
        "127.0.0.1",
        "--tap-no-live",
        "--tap-output-dir",
        str(tmp_path),
    ]


def test_monitor_controller_reuses_healthy_dashboard_and_starts_proxies(tmp_path: Path) -> None:
    spawned: list[list[str]] = []
    injected: list[tuple[int, int]] = []
    active = False

    class FakeProcess:
        def poll(self) -> int | None:
            return None

    def fake_popen(cmd: list[str], **_kwargs: object) -> FakeProcess:
        spawned.append(cmd)
        return FakeProcess()

    def fake_enable_injection(*, claude_port: int, codex_port: int, **_kwargs: object) -> None:
        nonlocal active
        injected.append((claude_port, codex_port))
        active = True

    controller = DashboardMonitorController(
        host="127.0.0.1",
        port=19527,
        output_dir=tmp_path,
        python_executable="/usr/bin/python3",
        popen=fake_popen,
        is_healthy=lambda _host, _port: True,
        enable_injection=fake_enable_injection,
        injection_is_active=lambda: active,
    )

    assert controller.start() == "http://127.0.0.1:19527"
    assert len(spawned) == 2
    assert all("dashboard" not in cmd for cmd in spawned)
    assert injected == [(19528, 19529)]
    assert controller.is_running() is True


def test_monitor_controller_spawns_dashboard_process(tmp_path: Path) -> None:
    spawned: list[tuple[list[str], dict[str, object]]] = []

    class FakeProcess:
        def poll(self) -> int | None:
            return None

    def fake_popen(cmd: list[str], **kwargs: object) -> FakeProcess:
        spawned.append((cmd, kwargs))
        return FakeProcess()

    controller = DashboardMonitorController(
        host="127.0.0.1",
        port=19527,
        output_dir=tmp_path,
        python_executable=sys.executable,
        popen=fake_popen,
        is_healthy=lambda _host, _port: False,
    )

    assert controller.start() == "http://127.0.0.1:19527"
    assert len(spawned) == 3
    cmd, kwargs = spawned[0]
    assert cmd[:4] == [sys.executable, "-m", "claude_tap", "dashboard"]
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stdout"] == subprocess.DEVNULL
    assert kwargs["stderr"] == subprocess.DEVNULL
    assert kwargs["start_new_session"] is True


def test_monitor_controller_start_spawns_proxies_and_enables_global_injection(tmp_path: Path) -> None:
    spawned: list[list[str]] = []
    injected: list[tuple[int, int, list[dict[str, object]]]] = []

    class FakeProcess:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def poll(self) -> int | None:
            return None

    def fake_popen(cmd: list[str], **_kwargs: object) -> FakeProcess:
        spawned.append(cmd)
        return FakeProcess(4200 + len(spawned))

    controller = DashboardMonitorController(
        host="127.0.0.1",
        port=19527,
        output_dir=tmp_path,
        python_executable=sys.executable,
        popen=fake_popen,
        is_healthy=lambda _host, _port: False,
        enable_injection=lambda *, claude_port, codex_port, processes: injected.append(
            (claude_port, codex_port, processes)
        ),
    )

    assert controller.start() == "http://127.0.0.1:19527"

    assert len(spawned) == 3
    assert spawned[0][:4] == [sys.executable, "-m", "claude_tap", "dashboard"]
    assert spawned[1][3:7] == ["--tap-no-launch", "--tap-client", "claude", "--tap-port"]
    assert spawned[1][7] == "19528"
    assert spawned[2][3:7] == ["--tap-no-launch", "--tap-client", "codex", "--tap-port"]
    assert spawned[2][7] == "19529"
    assert injected == [
        (
            19528,
            19529,
            [
                {"pid": 4201, "role": "dashboard"},
                {"pid": 4202, "role": "claude proxy"},
                {"pid": 4203, "role": "codex proxy"},
            ],
        )
    ]


def test_monitor_controller_start_returns_existing_monitor_url(tmp_path: Path) -> None:
    class FakeProcess:
        def poll(self) -> int | None:
            return None

    controller = DashboardMonitorController(
        host="127.0.0.1",
        port=19527,
        output_dir=tmp_path,
        popen=lambda _cmd, **_kwargs: pytest.fail("already-running monitor should not spawn"),
        is_healthy=lambda _host, _port: True,
        injection_is_active=lambda: True,
    )
    controller._proxy_processes = [FakeProcess(), FakeProcess()]  # type: ignore[list-item]

    assert controller.start() == "http://127.0.0.1:19527"


def test_monitor_controller_recognizes_active_monitor_after_app_relaunch(tmp_path: Path) -> None:
    controller = DashboardMonitorController(
        host="127.0.0.1",
        port=19527,
        output_dir=tmp_path,
        popen=lambda _cmd, **_kwargs: pytest.fail("already-running monitor should not spawn"),
        is_healthy=lambda _host, _port: True,
        injection_is_active=lambda: True,
        recorded_proxy_processes_are_running=lambda **_kwargs: True,
    )

    assert controller.is_running() is True


def test_monitor_controller_running_when_dashboard_health_check_fails(tmp_path: Path) -> None:
    """A reused/version-mismatched dashboard (health check False) must not make a
    running monitor (active injection + live proxies) read as stopped."""

    class FakeProcess:
        def poll(self) -> int | None:
            return None

    controller = DashboardMonitorController(
        host="127.0.0.1",
        port=19527,
        output_dir=tmp_path,
        popen=lambda _cmd, **_kwargs: pytest.fail("running monitor should not spawn"),
        is_healthy=lambda _host, _port: False,
        injection_is_active=lambda: True,
    )
    controller._proxy_processes = [FakeProcess(), FakeProcess()]  # type: ignore[list-item]

    assert controller.is_running() is True


def test_monitor_controller_open_dashboard_does_not_restart_running_monitor(tmp_path: Path) -> None:
    """Open Dashboard on a running monitor must open the browser directly without
    re-spawning proxies or re-injecting, even when the dashboard health check fails."""
    opened: list[str] = []

    class FakeProcess:
        def poll(self) -> int | None:
            return None

    controller = DashboardMonitorController(
        host="127.0.0.1",
        port=19527,
        output_dir=tmp_path,
        popen=lambda _cmd, **_kwargs: pytest.fail("running monitor should not spawn proxies"),
        is_healthy=lambda _host, _port: False,
        injection_is_active=lambda: True,
        enable_injection=lambda **_kwargs: pytest.fail("running monitor should not re-inject"),
        open_browser=opened.append,
    )
    controller._proxy_processes = [FakeProcess(), FakeProcess()]  # type: ignore[list-item]

    controller.open_dashboard()

    assert opened == ["http://127.0.0.1:19527"]


def test_monitor_controller_stop_clears_dead_owned_processes(tmp_path: Path) -> None:
    class DeadProcess:
        def poll(self) -> int:
            return 0

    controller = DashboardMonitorController(
        host="127.0.0.1",
        port=19527,
        output_dir=tmp_path,
        is_healthy=lambda _host, _port: False,
        injection_is_active=lambda: False,
    )
    controller._process = DeadProcess()  # type: ignore[assignment]
    controller._proxy_processes = [DeadProcess()]  # type: ignore[list-item]
    controller._proxy_process_names = ["claude"]

    assert controller.stop() is False
    assert controller._process is None
    assert controller._proxy_processes == []
    assert controller._proxy_process_names == []


def test_monitor_controller_kills_process_that_ignores_terminate(tmp_path: Path) -> None:
    events: list[str] = []

    class StubbornProcess:
        def __init__(self) -> None:
            self.waits = 0

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            events.append("terminate")

        def kill(self) -> None:
            events.append("kill")

        def wait(self, timeout: float) -> int:
            self.waits += 1
            events.append(f"wait:{timeout}")
            if self.waits == 1:
                raise subprocess.TimeoutExpired("claude-tap", timeout)
            return 0

    controller = DashboardMonitorController(host="127.0.0.1", port=19527, output_dir=tmp_path)
    controller._terminate_process(StubbornProcess())  # type: ignore[arg-type]

    assert events == ["terminate", "wait:5.0", "kill", "wait:5.0"]


def test_monitor_controller_open_dashboard_starts_and_opens_browser(tmp_path: Path) -> None:
    opened: list[str] = []

    class FakeProcess:
        def poll(self) -> int | None:
            return None

    controller = DashboardMonitorController(
        host="127.0.0.1",
        port=19527,
        output_dir=tmp_path,
        popen=lambda _cmd, **_kwargs: FakeProcess(),
        is_healthy=lambda _host, _port: True,
        injection_is_active=lambda: True,
        open_browser=opened.append,
    )

    assert controller.can_stop() is True
    controller.open_dashboard()

    assert opened == ["http://127.0.0.1:19527"]


def test_monitor_controller_start_fails_when_dashboard_exits_immediately(tmp_path: Path) -> None:
    class FakeProcess:
        returncode = 3

        def poll(self) -> int:
            return self.returncode

    controller = DashboardMonitorController(
        host="127.0.0.1",
        port=19527,
        output_dir=tmp_path,
        popen=lambda _cmd, **_kwargs: FakeProcess(),
        is_healthy=lambda _host, _port: False,
        startup_check_delay=0,
    )

    with pytest.raises(RuntimeError, match="dashboard exited with code 3"):
        controller.start()


def test_monitor_controller_subprocess_kwargs_include_windows_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        "claude_tap.process_utils.windows_no_console_subprocess_kwargs",
        lambda: {"creationflags": 123},
    )

    kwargs = DashboardMonitorController._subprocess_kwargs()

    assert kwargs["creationflags"] == 123
    assert "start_new_session" not in kwargs


def test_monitor_controller_restores_stale_injection_before_spawning_proxies(tmp_path: Path) -> None:
    events: list[str] = []

    class FakeProcess:
        def poll(self) -> int | None:
            return None

    def fake_popen(_cmd: list[str], **_kwargs: object) -> FakeProcess:
        events.append("spawn")
        return FakeProcess()

    controller = DashboardMonitorController(
        host="127.0.0.1",
        port=19527,
        output_dir=tmp_path,
        python_executable=sys.executable,
        popen=fake_popen,
        is_healthy=lambda _host, _port: True,
        injection_is_active=lambda: True,
        disable_injection=lambda: events.append("restore"),
        enable_injection=lambda **_kwargs: events.append("inject"),
    )

    controller.start()

    assert events == ["restore", "spawn", "spawn", "inject"]


def test_monitor_controller_start_fails_when_proxy_exits_immediately(tmp_path: Path) -> None:
    injected: list[str] = []
    restored: list[str] = []
    events: list[str] = []

    class FakeProcess:
        def __init__(self, name: str, returncode: int | None) -> None:
            self.name = name
            self.returncode = returncode

        def poll(self) -> int | None:
            return self.returncode

        def terminate(self) -> None:
            events.append(f"terminate:{self.name}")
            self.returncode = 0

        def wait(self, timeout: float) -> int:
            events.append(f"wait:{self.name}:{timeout}")
            return 0

    processes = iter([FakeProcess("claude", 2), FakeProcess("codex", None)])

    controller = DashboardMonitorController(
        host="127.0.0.1",
        port=19527,
        output_dir=tmp_path,
        python_executable=sys.executable,
        popen=lambda _cmd, **_kwargs: next(processes),
        is_healthy=lambda _host, _port: True,
        enable_injection=lambda **_kwargs: injected.append("inject"),
        disable_injection=lambda: restored.append("restore"),
        startup_check_delay=0,
    )

    with pytest.raises(RuntimeError, match="claude proxy exited with code 2"):
        controller.start()

    assert injected == []
    assert restored == ["restore"]
    assert events == ["terminate:codex", "wait:codex:5.0"]
    assert controller.is_running() is False


def test_monitor_controller_stop_terminates_owned_process(tmp_path: Path) -> None:
    events: list[str] = []

    class FakeProcess:
        def __init__(self) -> None:
            self.running = True

        def poll(self) -> int | None:
            return None if self.running else 0

        def terminate(self) -> None:
            events.append("terminate")
            self.running = False

        def wait(self, timeout: float) -> int:
            events.append(f"wait:{timeout}")
            return 0

    process = FakeProcess()

    controller = DashboardMonitorController(
        host="127.0.0.1",
        port=19527,
        output_dir=tmp_path,
        python_executable=sys.executable,
        popen=lambda _cmd, **_kwargs: process,
        is_healthy=lambda _host, _port: False,
    )
    controller.start()

    assert controller.stop() is True
    assert events == ["terminate", "wait:5.0"]
    assert controller.is_running() is False


def test_monitor_controller_stop_restores_injection_and_stops_all_processes(tmp_path: Path) -> None:
    events: list[str] = []
    restored: list[str] = []

    class FakeProcess:
        def __init__(self, name: str) -> None:
            self.name = name
            self.running = True

        def poll(self) -> int | None:
            return None if self.running else 0

        def terminate(self) -> None:
            events.append(f"terminate:{self.name}")
            self.running = False

        def wait(self, timeout: float) -> int:
            events.append(f"wait:{self.name}:{timeout}")
            return 0

    processes = iter([FakeProcess("dashboard"), FakeProcess("claude"), FakeProcess("codex")])

    controller = DashboardMonitorController(
        host="127.0.0.1",
        port=19527,
        output_dir=tmp_path,
        python_executable=sys.executable,
        popen=lambda _cmd, **_kwargs: next(processes),
        is_healthy=lambda _host, _port: False,
        disable_injection=lambda: restored.append("restore"),
    )
    controller.start()

    assert controller.stop() is True
    assert restored == ["restore"]
    assert events == [
        "terminate:dashboard",
        "wait:dashboard:5.0",
        "terminate:claude",
        "wait:claude:5.0",
        "terminate:codex",
        "wait:codex:5.0",
    ]


def test_parse_macos_app_args_ignores_launch_services_process_serial_number() -> None:
    args = parse_macos_app_args(["-psn_0_12345", "--tap-no-auto-start"])

    assert args.auto_start is False


def test_menu_app_start_monitor_shows_error_on_failure() -> None:
    class FakeController:
        def start(self) -> str:
            raise RuntimeError("port 19528 is already in use")

    app = object.__new__(MacOSMenuApp)
    app.controller = FakeController()
    calls: list[str] = []
    errors: list[tuple[str, str]] = []
    app.refresh_menu = lambda: calls.append("refresh")  # type: ignore[method-assign]
    app._show_error = lambda title, details: errors.append((title, details))  # type: ignore[method-assign]
    app._confirm_start_monitor = lambda: True  # type: ignore[method-assign]

    app.start_monitor()

    assert calls == ["refresh"]
    assert errors == [("Unable to start Claude Tap monitor", "port 19528 is already in use")]


def test_menu_app_start_monitor_success_refreshes_menu() -> None:
    class FakeController:
        def __init__(self) -> None:
            self.started = False

        def start(self) -> str:
            self.started = True
            return "http://127.0.0.1:19527"

    app = object.__new__(MacOSMenuApp)
    app.controller = FakeController()
    calls: list[str] = []
    app.refresh_menu = lambda: calls.append("refresh")  # type: ignore[method-assign]
    app._confirm_start_monitor = lambda: True  # type: ignore[method-assign]

    app.start_monitor()

    assert app.controller.started is True
    assert calls == ["refresh"]


def test_menu_app_start_monitor_cancel_does_not_start() -> None:
    class FakeController:
        def start(self) -> str:
            pytest.fail("start should not be called")

    app = object.__new__(MacOSMenuApp)
    app.controller = FakeController()
    calls: list[str] = []
    app.refresh_menu = lambda: calls.append("refresh")  # type: ignore[method-assign]
    app._confirm_start_monitor = lambda: False  # type: ignore[method-assign]

    app.start_monitor()

    assert calls == ["refresh"]


def test_menu_app_open_dashboard_cancel_does_not_start_when_stopped() -> None:
    class FakeController:
        def is_running(self) -> bool:
            return False

        def open_dashboard(self) -> None:
            pytest.fail("open dashboard should not start without confirmation")

    app = object.__new__(MacOSMenuApp)
    app.controller = FakeController()
    calls: list[str] = []
    app.refresh_menu = lambda: calls.append("refresh")  # type: ignore[method-assign]
    app._confirm_start_monitor = lambda: False  # type: ignore[method-assign]

    app.open_dashboard()

    assert calls == ["refresh"]


def test_menu_app_open_dashboard_running_does_not_confirm() -> None:
    events: list[str] = []

    class FakeController:
        def is_running(self) -> bool:
            return True

        def open_dashboard(self) -> None:
            events.append("open")

    app = object.__new__(MacOSMenuApp)
    app.controller = FakeController()
    app.refresh_menu = lambda: events.append("refresh")  # type: ignore[method-assign]
    app._confirm_start_monitor = lambda: pytest.fail("running monitor should open dashboard without confirmation")

    app.open_dashboard()

    assert events == ["open", "refresh"]


def test_menu_app_open_dashboard_active_monitor_after_relaunch_does_not_confirm(tmp_path: Path) -> None:
    opened: list[str] = []

    controller = DashboardMonitorController(
        host="127.0.0.1",
        port=19527,
        output_dir=tmp_path,
        popen=lambda _cmd, **_kwargs: pytest.fail("already-running monitor should not spawn"),
        is_healthy=lambda _host, _port: True,
        injection_is_active=lambda: True,
        recorded_proxy_processes_are_running=lambda **_kwargs: True,
        open_browser=opened.append,
    )
    app = object.__new__(MacOSMenuApp)
    app.controller = controller
    app.refresh_menu = lambda: None  # type: ignore[method-assign]
    app._confirm_start_monitor = lambda: pytest.fail("active monitor should open dashboard without confirmation")

    app.open_dashboard()

    assert opened == ["http://127.0.0.1:19527"]


class FakeObjC:
    def __init__(self) -> None:
        self.calls: list[tuple[int, str, tuple[object, ...]]] = []
        self.strings: dict[int, str] = {}
        self._next = 100
        self.objc = FakeRuntime(self)

    def _id(self) -> int:
        self._next += 1
        return self._next

    def cls(self, _name: str) -> int:
        return self._id()

    def sel(self, name: str | None) -> int | None:
        return None if name is None else self._id()

    def nsstring(self, value: str) -> int:
        ident = self._id()
        self.strings[ident] = value
        return ident

    def alloc_init(self, _class_name: str) -> int:
        return self._id()

    def msg(
        self,
        receiver: int,
        selector: str,
        _restype: Any = None,
        _argtypes: list[Any] | None = None,
        *args: object,
    ) -> int:
        self.calls.append((receiver, selector, args))
        if selector == "runModal":
            return macos_app._NS_ALERT_FIRST_BUTTON_RETURN
        return self._id()


class FakeRuntime:
    def __init__(self, objc: FakeObjC) -> None:
        self.objc = objc
        self.registered: list[int] = []
        self.methods: list[bytes] = []

    def objc_getClass(self, name: bytes) -> int:
        return 0 if name == b"ClaudeTapMenuTarget" else 900

    def objc_allocateClassPair(self, _base: int, _name: bytes, _extra: int) -> int:
        return 901

    def class_addMethod(self, _cls: int, _selector: int | None, _callback: object, types: bytes) -> bool:
        self.methods.append(types)
        return True

    def objc_registerClassPair(self, cls: int) -> None:
        self.registered.append(cls)


def test_menu_app_run_builds_menu_and_refreshes_without_auto_start(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_objc = FakeObjC()
    monkeypatch.setattr(macos_app, "_ObjC", lambda: fake_objc)
    monkeypatch.setattr(
        macos_app,
        "list_trace_sessions",
        lambda: [{"agent": "codex", "record_count": 2, "first_user": "hello from trace"}],
    )

    class FakeController:
        def is_running(self) -> bool:
            return False

        def can_stop(self) -> bool:
            return True

        def start(self) -> str:
            pytest.fail("auto_start=False should not start")

    app = MacOSMenuApp(FakeController(), auto_start=False)  # type: ignore[arg-type]

    assert app.run() == 0

    titles = [
        fake_objc.strings[arg]
        for _receiver, selector, args in fake_objc.calls
        for arg in args
        if selector == "setTitle:"
    ]
    assert "Monitor: Stopped" in titles
    assert "Sessions: 1" in titles
    assert "Latest: codex (2) - hello from trace" in titles


def test_menu_app_status_item_uses_dashboard_icon_without_visible_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_objc = FakeObjC()
    monkeypatch.setattr(macos_app, "_ObjC", lambda: fake_objc)
    monkeypatch.setattr(macos_app, "list_trace_sessions", lambda: [])

    class FakeController:
        def is_running(self) -> bool:
            return False

        def can_stop(self) -> bool:
            return False

        def start(self) -> str:
            pytest.fail("auto_start=False should not start")

    app = MacOSMenuApp(FakeController(), auto_start=False)  # type: ignore[arg-type]

    assert app.run() == 0

    image_symbols = [
        (fake_objc.strings[args[0]], fake_objc.strings[args[1]])
        for _receiver, selector, args in fake_objc.calls
        if selector == "imageWithSystemSymbolName:accessibilityDescription:"
    ]
    assert image_symbols == [("rectangle.grid.2x2", "Dashboard")]

    image_positions = [args[0] for _receiver, selector, args in fake_objc.calls if selector == "setImagePosition:"]
    assert image_positions == [macos_app._NS_IMAGE_ONLY]

    status_titles = [
        fake_objc.strings[args[0]]
        for _receiver, selector, args in fake_objc.calls
        if selector == "setTitle:" and args and args[0] in fake_objc.strings
    ]
    assert "" in status_titles
    assert "Claude" not in status_titles

    tooltips = [
        fake_objc.strings[args[0]] for _receiver, selector, args in fake_objc.calls if selector == "setToolTip:"
    ]
    assert tooltips == ["Dashboard"]


def test_menu_app_wrappers_refresh_and_quit() -> None:
    events: list[str] = []

    class FakeController:
        def is_running(self) -> bool:
            return True

        def stop(self) -> bool:
            events.append("stop")
            return True

        def open_dashboard(self) -> None:
            events.append("open")

    app = object.__new__(MacOSMenuApp)
    app.controller = FakeController()
    app._app = 77
    app._objc = FakeObjC()
    app.refresh_menu = lambda: events.append("refresh")  # type: ignore[method-assign]

    app.stop_monitor()
    app.open_dashboard()
    app.quit()

    assert events == ["stop", "refresh", "open", "refresh", "stop"]
    assert any(selector == "terminate:" for _receiver, selector, _args in app._objc.calls)


def test_menu_app_alert_helpers_use_objective_c_alerts() -> None:
    app = object.__new__(MacOSMenuApp)
    app._objc = FakeObjC()

    app._show_error("Title", "Details")

    assert app._confirm_start_monitor() is True
    strings = set(app._objc.strings.values())
    assert {"Title", "Details", "Start Claude Tap Monitor?", "Start Monitor", "Cancel"} <= strings


def test_objc_rejects_non_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")

    with pytest.raises(RuntimeError, match="only runs on macOS"):
        macos_app._ObjC()


def test_new_menu_target_registers_callbacks() -> None:
    fake_objc = FakeObjC()

    target = macos_app._new_menu_target(fake_objc)  # type: ignore[arg-type]

    assert target > 0
    assert fake_objc.objc.registered == [901]
    assert fake_objc.objc.methods == [b"v@:@"] * 5


def test_menu_callbacks_forward_to_active_app(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    class FakeApp:
        def start_monitor(self) -> None:
            events.append("start")

        def stop_monitor(self) -> None:
            events.append("stop")

        def open_dashboard(self) -> None:
            events.append("open")

        def refresh_menu(self) -> None:
            events.append("refresh")

        def quit(self) -> None:
            events.append("quit")

    monkeypatch.setattr(macos_app, "_ACTIVE_APP", FakeApp())

    macos_app._start_monitor_callback(0, 0, 0)
    macos_app._stop_monitor_callback(0, 0, 0)
    macos_app._open_dashboard_callback(0, 0, 0)
    macos_app._refresh_menu_callback(0, 0, 0)
    macos_app._quit_callback(0, 0, 0)

    assert events == ["start", "stop", "open", "refresh", "quit"]


def test_main_entry_routes_macos_app_subcommand(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, object] = {}

    def fake_macos_main(argv: list[str]) -> int:
        called["argv"] = argv
        return 4

    monkeypatch.setattr(sys, "argv", ["claude-tap", "macos-app", "--tap-no-auto-start"])
    monkeypatch.setattr("claude_tap.macos_app.main", fake_macos_main)

    with pytest.raises(SystemExit) as excinfo:
        main_entry()

    assert excinfo.value.code == 4
    assert called["argv"] == ["--tap-no-auto-start"]
