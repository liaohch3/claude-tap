"""Regression tests for Windows-specific bugs reported in issue #83.

Patches `signal` and `shutil.which` to simulate the Windows environment so the
tests run identically on Linux/macOS CI and on real Windows.
"""

from __future__ import annotations

import asyncio
import signal
import sys

import pytest

from claude_tap.cli import run_client


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
    assert captured["cmd"] == (shim_path, "--version")


def test_module_import_reconfigures_stdout_to_utf8() -> None:
    import claude_tap.cli  # noqa: F401

    for stream in (sys.stdout, sys.stderr):
        encoding = getattr(stream, "encoding", "")
        assert encoding and encoding.lower().replace("-", "") == "utf8", f"expected UTF-8, got {encoding!r} on {stream}"
