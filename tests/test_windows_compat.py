"""Regression tests for Windows-specific bugs reported in issue #83.

Patches `signal` and `shutil.which` to simulate the Windows environment so the
tests run identically on Linux/macOS CI and on real Windows.
"""

from __future__ import annotations

import asyncio
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from claude_tap.cli import _start_background_update, run_client
from claude_tap.history import _rel_posix


class _DummyProc:
    def __init__(self) -> None:
        self.pid = 12345
        self.returncode: int | None = None

    async def wait(self) -> int:
        self.returncode = 0
        return 0

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9


def _strip_sigtstp(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove SIGTSTP and force loop.add/remove_signal_handler to NotImplementedError."""
    if hasattr(signal, "SIGTSTP"):
        monkeypatch.delattr(signal, "SIGTSTP", raising=False)

    def _not_impl(*_args, **_kwargs):
        raise NotImplementedError

    monkeypatch.setattr("asyncio.AbstractEventLoop.add_signal_handler", _not_impl, raising=False)
    monkeypatch.setattr("asyncio.AbstractEventLoop.remove_signal_handler", _not_impl, raising=False)


@pytest.mark.asyncio
async def test_run_client_does_not_touch_sigtstp_when_absent(monkeypatch) -> None:
    async def fake_create_subprocess_exec(*cmd, **kwargs):
        return _DummyProc()

    _strip_sigtstp(monkeypatch)
    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: r"C:\Users\x\.local\bin\claude.cmd")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(43123, ["--version"], client="claude", proxy_mode="reverse")
    assert code == 0


@pytest.mark.asyncio
async def test_run_client_passes_resolved_path_for_cmd_shim(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        return _DummyProc()

    shim_path = r"C:\Users\x\.local\bin\claude.cmd"
    _strip_sigtstp(monkeypatch)
    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: shim_path)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(43123, ["--version"], client="claude", proxy_mode="reverse")
    assert code == 0
    cmd = captured["cmd"]
    assert cmd[0] == shim_path, "resolved .cmd shim path must be preserved"
    assert cmd[1] == "--settings", "--settings must be injected before forwarded args"
    import json

    injected = json.loads(cmd[2])
    assert injected == {"env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:43123"}}
    assert cmd[3:] == ("--version",), "original args must follow --settings payload"


@pytest.mark.asyncio
async def test_run_client_uses_wrapper_provided_claude_binary(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        return _DummyProc()

    wrapped_claude = tmp_path / "claude"
    wrapped_claude.write_text("#!/bin/sh\n", encoding="utf-8")
    _strip_sigtstp(monkeypatch)
    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: None)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(
        43123,
        ["--output-format", "stream-json"],
        client="claude",
        proxy_mode="reverse",
        client_cmd=str(wrapped_claude),
    )
    assert code == 0
    cmd = captured["cmd"]
    assert cmd[0] == str(wrapped_claude)
    assert cmd[1] == "--settings"
    assert cmd[3:] == ("--output-format", "stream-json")


@pytest.mark.asyncio
async def test_run_client_does_not_execute_wrapper_directory(monkeypatch, tmp_path: Path) -> None:
    async def fail_create_subprocess_exec(*cmd, **kwargs):
        raise AssertionError(f"directory path should not be executed: {cmd}")

    wrapped_dir = tmp_path / "claude"
    wrapped_dir.mkdir()
    _strip_sigtstp(monkeypatch)
    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: None)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(
        43123,
        ["--output-format", "stream-json"],
        client="claude",
        proxy_mode="reverse",
        client_cmd=str(wrapped_dir),
    )
    assert code == 1


def test_module_import_reconfigures_stdout_to_utf8() -> None:
    import claude_tap.cli  # noqa: F401

    for stream in (sys.stdout, sys.stderr):
        encoding = getattr(stream, "encoding", "")
        assert encoding and encoding.lower().replace("-", "") == "utf8", f"expected UTF-8, got {encoding!r} on {stream}"


def test_rel_posix_uses_forward_slashes(tmp_path: Path) -> None:
    nested = tmp_path / "2026-04-29" / "trace_001234.jsonl"
    nested.parent.mkdir(parents=True)
    nested.write_text("{}\n", encoding="utf-8")
    assert _rel_posix(nested, tmp_path) == "2026-04-29/trace_001234.jsonl"


def test_start_background_update_resolves_uv_shim(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return object()

    shim = r"C:\Users\x\AppData\Local\Programs\uv\uv.cmd"
    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda name: shim if name == "uv" else None)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    _start_background_update("uv")
    assert captured["cmd"] == [shim, "tool", "upgrade", "claude-tap"]


def test_start_background_update_hides_windows_console(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeStartupInfo:
        def __init__(self) -> None:
            self.dwFlags = 0
            self.wShowWindow: int | None = None

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return object()

    shim = r"C:\Users\x\AppData\Local\Programs\uv\uv.cmd"
    monkeypatch.setattr("claude_tap.cli_update.sys.platform", "win32")
    monkeypatch.setattr("claude_tap.cli_update.shutil.which", lambda name: shim if name == "uv" else None)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x1000, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x2000, raising=False)
    monkeypatch.setattr(subprocess, "STARTF_USESHOWWINDOW", 0x4000, raising=False)
    monkeypatch.setattr(subprocess, "SW_HIDE", 0, raising=False)
    monkeypatch.setattr(subprocess, "STARTUPINFO", FakeStartupInfo, raising=False)

    _start_background_update("uv")

    assert captured["cmd"] == [shim, "tool", "upgrade", "claude-tap"]
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stdout"] == subprocess.DEVNULL
    assert kwargs["stderr"] == subprocess.DEVNULL
    assert kwargs["creationflags"] == 0x1000 | 0x2000
    startupinfo = kwargs["startupinfo"]
    assert isinstance(startupinfo, FakeStartupInfo)
    assert startupinfo.dwFlags == 0x4000
    assert startupinfo.wShowWindow == 0


def test_start_background_update_returns_none_when_uv_missing(monkeypatch) -> None:
    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: None)
    assert _start_background_update("uv") is None
