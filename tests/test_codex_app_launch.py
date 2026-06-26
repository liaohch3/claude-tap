from __future__ import annotations

import asyncio
import builtins
import subprocess
from pathlib import Path

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


@pytest.mark.asyncio
async def test_run_client_codexapp_forward_launches_app_with_proxy_env(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, object] = {}
    ca_path = Path("/tmp/test-ca.pem")

    async def fake_create_subprocess_exec(*cmd: str, **kwargs: object) -> _DummyProc:
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        captured["stdin"] = kwargs["stdin"]
        captured["stdout"] = kwargs["stdout"]
        captured["stderr"] = kwargs["stderr"]
        return _DummyProc()

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/Applications/Codex.app/Contents/MacOS/Codex")
    monkeypatch.setattr("claude_tap.cli_clients._codex_app_existing_processes", lambda: [])
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(
        43123,
        [],
        client="codexapp",
        proxy_mode="forward",
        ca_cert_path=ca_path,
    )

    env = captured["env"]
    assert code == 0
    assert captured["cmd"] == ("/Applications/Codex.app/Contents/MacOS/Codex",)
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:43123"
    assert env["SSL_CERT_FILE"] == str(ca_path)
    assert env["CODEX_CA_CERTIFICATE"] == str(ca_path)
    assert captured["stdin"] == subprocess.DEVNULL
    assert captured["stdout"] == subprocess.DEVNULL
    assert captured["stderr"] == subprocess.DEVNULL
    out = capsys.readouterr().out
    assert "Codex App exited immediately" in out
    assert "already-running Codex App" in out


@pytest.mark.asyncio
async def test_run_client_codexapp_forward_aborts_when_existing_app_noninteractive(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fail_create_subprocess_exec(*_cmd: str, **_kwargs: object) -> _DummyProc:
        raise AssertionError("Codex App should not launch while an existing app is running")

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/Applications/Codex.app/Contents/MacOS/Codex")
    monkeypatch.setattr(
        "claude_tap.cli_clients._codex_app_existing_processes",
        lambda: ["123 /Applications/Codex.app/Contents/MacOS/Codex"],
    )
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(43123, [], client="codexapp", proxy_mode="forward")

    assert code == 1
    out = capsys.readouterr().out
    assert "Codex App is already running" in out
    assert "Quit Codex App completely" in out


@pytest.mark.asyncio
async def test_run_client_codexapp_forward_prompts_to_quit_existing_app(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, object] = {}
    processes = [["123 /Applications/Codex.app/Contents/MacOS/Codex"], []]
    quit_called = False

    async def fake_create_subprocess_exec(*cmd: str, **kwargs: object) -> _DummyProc:
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        captured["stdin"] = kwargs["stdin"]
        captured["stdout"] = kwargs["stdout"]
        captured["stderr"] = kwargs["stderr"]
        return _DummyProc()

    def fake_existing_processes() -> list[str]:
        return processes[0]

    def fake_quit_codex_app() -> bool:
        nonlocal quit_called
        quit_called = True
        processes.pop(0)
        return True

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/Applications/Codex.app/Contents/MacOS/Codex")
    monkeypatch.setattr("claude_tap.cli_clients._codex_app_existing_processes", fake_existing_processes)
    monkeypatch.setattr("claude_tap.cli_clients._quit_codex_app", fake_quit_codex_app)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(builtins, "input", lambda _prompt: "y")

    code = await run_client(43123, [], client="codexapp", proxy_mode="forward")

    assert code == 0
    assert quit_called is True
    assert captured["cmd"] == ("/Applications/Codex.app/Contents/MacOS/Codex",)
    out = capsys.readouterr().out
    assert "Codex App is already running" in out
    assert "Codex App exited. Starting a proxied instance now." in out
