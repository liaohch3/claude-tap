from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from claude_tap.cli import main_entry
from claude_tap.macos_app import (
    DashboardMonitorController,
    build_dashboard_command,
    build_proxy_command,
    parse_macos_app_args,
)


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

    class FakeProcess:
        def poll(self) -> int | None:
            return None

    def fake_popen(cmd: list[str], **_kwargs: object) -> FakeProcess:
        spawned.append(cmd)
        return FakeProcess()

    controller = DashboardMonitorController(
        host="127.0.0.1",
        port=19527,
        output_dir=tmp_path,
        python_executable="/usr/bin/python3",
        popen=fake_popen,
        is_healthy=lambda _host, _port: True,
        enable_injection=lambda *, claude_port, codex_port: injected.append((claude_port, codex_port)),
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
    injected: list[tuple[int, int]] = []

    class FakeProcess:
        def poll(self) -> int | None:
            return None

    def fake_popen(cmd: list[str], **_kwargs: object) -> FakeProcess:
        spawned.append(cmd)
        return FakeProcess()

    controller = DashboardMonitorController(
        host="127.0.0.1",
        port=19527,
        output_dir=tmp_path,
        python_executable=sys.executable,
        popen=fake_popen,
        is_healthy=lambda _host, _port: False,
        enable_injection=lambda *, claude_port, codex_port: injected.append((claude_port, codex_port)),
    )

    assert controller.start() == "http://127.0.0.1:19527"

    assert len(spawned) == 3
    assert spawned[0][:4] == [sys.executable, "-m", "claude_tap", "dashboard"]
    assert spawned[1][3:7] == ["--tap-no-launch", "--tap-client", "claude", "--tap-port"]
    assert spawned[1][7] == "19528"
    assert spawned[2][3:7] == ["--tap-no-launch", "--tap-client", "codex", "--tap-port"]
    assert spawned[2][7] == "19529"
    assert injected == [(19528, 19529)]


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
