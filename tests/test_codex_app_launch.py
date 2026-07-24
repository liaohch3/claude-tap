from __future__ import annotations

import asyncio
import builtins
import os
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from claude_tap import cli_clients
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


def test_codex_app_existing_processes_filters_current_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    current_pid = os.getpid()

    def fake_run(cmd: list[str], **kwargs: object) -> SimpleNamespace:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout=(
                f"{current_pid} /Applications/Codex.app/Contents/MacOS/Codex\n"
                "123 /Applications/Codex.app/Contents/Resources/codex app-server\n"
            ),
        )

    monkeypatch.setattr(cli_clients.sys, "platform", "darwin")
    monkeypatch.setattr(cli_clients.subprocess, "run", fake_run)

    assert cli_clients._codex_app_existing_processes() == [
        "123 /Applications/Codex.app/Contents/Resources/codex app-server"
    ]
    assert captured["cmd"] == ["pgrep", "-fl", f"({cli_clients._CODEX_APP_PROCESS_RE})"]
    assert captured["kwargs"]["timeout"] == 2


def test_codex_app_existing_processes_matches_custom_executable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configured = tmp_path / "Codex Dev"
    captured: dict[str, object] = {}

    def fake_run(cmd: list[str], **_kwargs: object) -> SimpleNamespace:
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout=f"123 {configured}\n")

    monkeypatch.setattr(cli_clients.sys, "platform", "darwin")
    monkeypatch.setenv(cli_clients._CODEX_APP_EXECUTABLE_ENV, str(configured))
    monkeypatch.setattr(cli_clients.subprocess, "run", fake_run)

    assert cli_clients._codex_app_existing_processes() == [f"123 {configured}"]
    assert captured["cmd"] == [
        "pgrep",
        "-fl",
        f"({cli_clients._CODEX_APP_PROCESS_RE}|{re.escape(str(configured))})",
    ]


def test_codex_app_existing_processes_handles_unsupported_platform_and_pgrep_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_clients.sys, "platform", "linux")
    assert cli_clients._codex_app_existing_processes() == []

    monkeypatch.setattr(cli_clients.sys, "platform", "darwin")
    monkeypatch.setattr(
        cli_clients.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=2, stdout="")
    )
    assert cli_clients._codex_app_existing_processes() == []

    def raise_os_error(*_args: object, **_kwargs: object) -> SimpleNamespace:
        raise OSError("pgrep unavailable")

    monkeypatch.setattr(cli_clients.subprocess, "run", raise_os_error)
    assert cli_clients._codex_app_existing_processes() == []


def test_quit_codex_app_uses_bundle_id_and_reports_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(cmd: list[str], **kwargs: object) -> SimpleNamespace:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(cli_clients.subprocess, "run", fake_run)

    assert cli_clients._quit_codex_app() is True
    assert captured["cmd"] == ["osascript", "-e", 'tell application id "com.openai.codex" to quit']
    assert captured["kwargs"]["timeout"] == 5

    monkeypatch.setattr(cli_clients.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=1))
    assert cli_clients._quit_codex_app() is False

    def raise_subprocess_error(*_args: object, **_kwargs: object) -> SimpleNamespace:
        raise subprocess.SubprocessError("osascript failed")

    monkeypatch.setattr(cli_clients.subprocess, "run", raise_subprocess_error)
    assert cli_clients._quit_codex_app() is False


def test_codex_app_executable_candidates_prefers_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli_clients.sys, "platform", "darwin")
    monkeypatch.setenv(cli_clients._CODEX_APP_EXECUTABLE_ENV, "~/custom/Codex")

    candidates = cli_clients._codex_app_executable_candidates()

    assert candidates[0] == Path("~/custom/Codex").expanduser()
    assert Path("/Applications/Codex.app/Contents/MacOS/Codex") in candidates
    assert Path.home() / "Applications/Codex.app/Contents/MacOS/Codex" in candidates


def test_codex_app_executable_candidates_empty_on_non_macos_without_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_clients.sys, "platform", "linux")
    monkeypatch.delenv(cli_clients._CODEX_APP_EXECUTABLE_ENV, raising=False)

    assert cli_clients._codex_app_executable_candidates() == ()


def test_resolve_client_executable_uses_env_override_before_default_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configured = tmp_path / "Codex"
    configured.write_text("")
    monkeypatch.setenv(cli_clients._CODEX_APP_EXECUTABLE_ENV, str(configured))
    monkeypatch.setattr(
        cli_clients,
        "_codex_app_executable_candidates",
        lambda: (configured, Path("/Applications/Codex.app/Contents/MacOS/Codex")),
    )

    cfg = cli_clients.CLIENT_CONFIGS["codexapp"]
    resolved = cli_clients._resolve_client_executable("codexapp", cfg, None)

    assert resolved == str(configured)


def test_resolve_client_executable_returns_none_when_no_candidate_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_clients, "_codex_app_executable_candidates", lambda: (Path("/nonexistent/Codex"),))

    cfg = cli_clients.CLIENT_CONFIGS["codexapp"]
    assert cli_clients._resolve_client_executable("codexapp", cfg, None) is None


def test_resolve_client_executable_prefers_explicit_client_cmd(tmp_path: Path) -> None:
    wrapper_cmd = tmp_path / "codex-wrapper"
    wrapper_cmd.write_text("")
    wrapper_cmd.chmod(wrapper_cmd.stat().st_mode | 0o100)

    cfg = cli_clients.CLIENT_CONFIGS["codexapp"]
    resolved = cli_clients._resolve_client_executable("codexapp", cfg, str(wrapper_cmd))

    assert resolved == str(wrapper_cmd)


def test_resolve_client_executable_falls_back_to_path_lookup_for_other_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_clients.shutil, "which", lambda cmd: f"/usr/local/bin/{cmd}")

    cfg = cli_clients.CLIENT_CONFIGS["claude"]
    resolved = cli_clients._resolve_client_executable("claude", cfg, None)

    assert resolved == "/usr/local/bin/claude"


@pytest.mark.asyncio
async def test_wait_for_codex_app_exit_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_clients, "_codex_app_existing_processes", lambda: ["123 Codex"])

    async def fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    assert await cli_clients._wait_for_codex_app_exit(timeout_seconds=0) is False


@pytest.mark.asyncio
async def test_prepare_codex_app_forward_launch_handles_decline_quit_failure_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli_clients,
        "_codex_app_existing_processes",
        lambda: [
            "123 /Applications/Codex.app/Contents/MacOS/Codex",
            "124 /Applications/Codex.app/Contents/Resources/codex app-server",
            "125 /Applications/Codex.app/Contents/Resources/codex app-server",
            "126 /Applications/Codex.app/Contents/Resources/codex app-server",
        ],
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(builtins, "input", lambda _prompt: "n")

    assert await cli_clients._prepare_codex_app_forward_launch() is False
    assert "1 more process" in capsys.readouterr().out

    monkeypatch.setattr(builtins, "input", lambda _prompt: "y")
    monkeypatch.setattr(cli_clients, "_quit_codex_app", lambda: False)
    assert await cli_clients._prepare_codex_app_forward_launch() is False
    assert "Failed to send quit event" in capsys.readouterr().out

    monkeypatch.setattr(cli_clients, "_quit_codex_app", lambda: True)
    monkeypatch.setattr(cli_clients, "_wait_for_codex_app_exit", lambda: asyncio.sleep(0, result=False))
    assert await cli_clients._prepare_codex_app_forward_launch() is False
    assert "still running" in capsys.readouterr().out


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

    monkeypatch.setattr(
        "claude_tap.cli_clients._resolve_client_executable",
        lambda client, cfg, client_cmd: "/Applications/Codex.app/Contents/MacOS/Codex",
    )
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
    assert captured["cmd"] == (
        "/Applications/Codex.app/Contents/MacOS/Codex",
        "--proxy-server=http://127.0.0.1:43123",
    )
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

    monkeypatch.setattr(
        "claude_tap.cli_clients._resolve_client_executable",
        lambda client, cfg, client_cmd: "/Applications/Codex.app/Contents/MacOS/Codex",
    )
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

    monkeypatch.setattr(
        "claude_tap.cli_clients._resolve_client_executable",
        lambda client, cfg, client_cmd: "/Applications/Codex.app/Contents/MacOS/Codex",
    )
    monkeypatch.setattr("claude_tap.cli_clients._codex_app_existing_processes", fake_existing_processes)
    monkeypatch.setattr("claude_tap.cli_clients._quit_codex_app", fake_quit_codex_app)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(builtins, "input", lambda _prompt: "y")

    code = await run_client(43123, [], client="codexapp", proxy_mode="forward")

    assert code == 0
    assert quit_called is True
    assert captured["cmd"] == (
        "/Applications/Codex.app/Contents/MacOS/Codex",
        "--proxy-server=http://127.0.0.1:43123",
    )
    out = capsys.readouterr().out
    assert "Codex App is already running" in out
    assert "Codex App exited. Starting a proxied instance now." in out
