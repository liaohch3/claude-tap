"""Regression tests for Windows-specific bugs reported in issue #83.

Patches `signal` and `shutil.which` to simulate the Windows environment so the
tests run identically on Linux/macOS CI and on real Windows.
"""

from __future__ import annotations

import asyncio
import json
import signal
import sys
from pathlib import Path

import pytest

from claude_tap.cli import run_client
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
    injected = json.loads(cmd[2])
    assert injected == {"env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:43123"}}
    assert cmd[3:] == ("--version",), "original args must follow --settings payload"


@pytest.mark.asyncio
async def test_run_client_prefers_windows_cmd_sibling(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        return _DummyProc()

    posix_shim_path = tmp_path / "claude"
    posix_shim_path.write_text("#!/bin/sh\n", encoding="utf-8")
    cmd_shim_path = tmp_path / "claude.cmd"
    cmd_shim_path.write_text("@echo off\r\n", encoding="utf-8")
    _strip_sigtstp(monkeypatch)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(
        43123,
        ["--version"],
        client="claude",
        proxy_mode="reverse",
        client_cmd=str(posix_shim_path),
    )

    assert code == 0
    assert captured["cmd"][0] == str(cmd_shim_path)
    assert captured["cmd"][-1] == "--version"


@pytest.mark.skipif(sys.platform != "win32", reason="requires real Windows CreateProcess behavior")
@pytest.mark.asyncio
async def test_run_client_executes_real_cmd_shim_on_windows(monkeypatch, tmp_path: Path) -> None:
    """Prefer an npm .cmd sibling when given its extensionless POSIX shim."""
    shim_dir = tmp_path / "npm shims with spaces"
    shim_dir.mkdir()
    capture_path = shim_dir / "captured.json"
    client_script = shim_dir / "fake client.py"
    client_script.write_text(
        "import json, os, pathlib, sys\n"
        "pathlib.Path(os.environ['CLAUDE_TAP_TEST_CAPTURE']).write_text(\n"
        "    json.dumps({'argv': sys.argv[1:], 'base_url': os.environ.get('ANTHROPIC_BASE_URL')}),\n"
        "    encoding='utf-8',\n"
        ")\n",
        encoding="utf-8",
    )
    posix_shim_path = shim_dir / "claude"
    posix_shim_path.write_text('#!/bin/sh\necho "POSIX shim must not run on Windows"\n', encoding="utf-8")
    cmd_shim_path = shim_dir / "claude.cmd"
    cmd_shim_path.write_text(f'@echo off\r\n"{sys.executable}" "{client_script}" %*\r\n', encoding="utf-8")

    _strip_sigtstp(monkeypatch)
    monkeypatch.setenv("CLAUDE_TAP_TEST_CAPTURE", str(capture_path))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(
        43123,
        ["--version"],
        client="claude",
        proxy_mode="reverse",
        client_cmd=str(posix_shim_path),
    )

    assert code == 0
    captured = json.loads(capture_path.read_text(encoding="utf-8"))
    assert captured["argv"][2:] == ["--version"]
    assert json.loads(captured["argv"][1]) == {"env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:43123"}}
    assert captured["base_url"] == "http://127.0.0.1:43123"


@pytest.mark.asyncio
async def test_run_client_uses_wrapper_provided_claude_binary(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        return _DummyProc()

    wrapped_claude = tmp_path / "claude"
    wrapped_claude.write_text("#!/bin/sh\n", encoding="utf-8")
    wrapped_claude.chmod(wrapped_claude.stat().st_mode | 0o100)
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
