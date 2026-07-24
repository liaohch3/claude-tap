from __future__ import annotations

import asyncio
import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from claude_tap import cli_clients, parse_args
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


def _make_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _standalone_astron_config() -> cli_clients.ClientConfig:
    return cli_clients.ClientConfig(
        cmd="astron-code",
        label="Astron Code",
        install_url="https://www.npmjs.com/package/@iflytek/astron-code",
        base_url_env="",
        base_url_suffix="",
        default_target="",
        default_proxy_mode="forward",
    )


def test_astron_client_config_is_forward_only_and_unfiltered() -> None:
    cfg = cli_clients.CLIENT_CONFIGS["astron"]

    assert cfg.cmd == "astron-code"
    assert cfg.label == "Astron Code"
    assert cfg.default_proxy_mode == "forward"
    assert cfg.forward_trace_methods == ()
    assert cfg.forward_trace_path_prefixes == ()
    assert cfg.forward_base_url_envs == ()


def test_parse_args_astron_defaults_to_forward_proxy() -> None:
    args = parse_args(["--tap-client", "astron"])

    assert args.client == "astron"
    assert args.proxy_mode == "forward"


def test_parse_args_astron_rejects_reverse_proxy_with_specific_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        parse_args(["--tap-client", "astron", "--tap-proxy-mode", "reverse"])

    assert "--tap-client astron only supports forward proxy mode" in capsys.readouterr().err


def test_parse_args_public_client_cmd_wins_and_preserves_client_arguments(tmp_path: Path) -> None:
    explicit = _make_executable(tmp_path / "Astron Code" / "astron-code")

    args = parse_args(
        [
            "--tap-client",
            "astron",
            "--tap-client-cmd",
            str(explicit),
            "--",
            "exec",
            "hello",
        ]
    )

    assert args.client_cmd == str(explicit)
    assert args.claude_args == ["exec", "hello"]


def test_parse_args_does_not_treat_first_client_argument_as_astron_executable(tmp_path: Path) -> None:
    positional = _make_executable(tmp_path / "astron-code")

    args = parse_args(["--tap-client", "astron", "--", str(positional), "exec", "hello"])

    assert args.client_cmd is None
    assert args.claude_args == [str(positional), "exec", "hello"]


def test_resolve_astron_executable_prefers_path_without_considering_codex(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    looked_up: list[str] = []

    def fake_which(command: str) -> str | None:
        looked_up.append(command)
        if command == "astron-code":
            return "/current-node/bin/astron-code"
        if command == "codex":
            return "/unrelated/bin/codex"
        return None

    monkeypatch.setattr(cli_clients.shutil, "which", fake_which)

    resolved = cli_clients._resolve_client_executable("astron", _standalone_astron_config(), None)

    assert resolved == "/current-node/bin/astron-code"
    assert looked_up == ["astron-code"]


def test_resolve_astron_executable_uses_current_npm_global_prefix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    prefix = tmp_path / "npm global"
    candidate = _make_executable(prefix / "bin" / "astron-code")
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_which(command: str) -> str | None:
        return "/usr/local/bin/npm" if command == "npm" else None

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=f"{prefix}\n", stderr="")

    monkeypatch.setattr(cli_clients.sys, "platform", "darwin")
    monkeypatch.setattr(cli_clients.shutil, "which", fake_which)
    monkeypatch.setattr(cli_clients.subprocess, "run", fake_run)

    resolved = cli_clients._resolve_client_executable("astron", _standalone_astron_config(), None)

    assert resolved == str(candidate)
    assert calls[0][0] == ["/usr/local/bin/npm", "prefix", "-g"]
    assert calls[0][1]["check"] is False
    assert calls[0][1]["capture_output"] is True
    assert calls[0][1]["text"] is True
    assert calls[0][1]["timeout"] <= 5
    assert "shell" not in calls[0][1]


@pytest.mark.parametrize(
    ("returncode", "stdout"),
    [
        (1, "/unused"),
        (0, ""),
        (0, "/missing"),
    ],
)
def test_resolve_astron_executable_fails_closed_for_invalid_npm_discovery(
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    stdout: str,
) -> None:
    calls = 0

    def fake_which(command: str) -> str | None:
        return "/usr/bin/npm" if command == "npm" else None

    def fake_run(_command: list[str], **_kwargs: object) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="npm failed")

    monkeypatch.setattr(cli_clients.shutil, "which", fake_which)
    monkeypatch.setattr(cli_clients.subprocess, "run", fake_run)

    assert cli_clients._resolve_client_executable("astron", _standalone_astron_config(), None) is None
    assert calls == 1


def test_resolve_astron_executable_handles_npm_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def fake_which(command: str) -> str | None:
        return "/usr/bin/npm" if command == "npm" else None

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(cli_clients.shutil, "which", fake_which)
    monkeypatch.setattr(cli_clients.subprocess, "run", fake_run)

    assert cli_clients._resolve_client_executable("astron", _standalone_astron_config(), None) is None
    assert calls == 1


def test_resolve_astron_executable_prefers_windows_npm_cmd_shim(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    prefix = tmp_path / "npm"
    posix_shim = _make_executable(prefix / "astron-code")
    cmd_shim = prefix / "astron-code.cmd"
    cmd_shim.write_text("@echo off\r\n", encoding="utf-8")

    monkeypatch.setattr(cli_clients.sys, "platform", "win32")
    monkeypatch.setattr(
        cli_clients.shutil,
        "which",
        lambda command: r"C:\Program Files\nodejs\npm.cmd" if command == "npm" else None,
    )
    monkeypatch.setattr(
        cli_clients.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=str(prefix), stderr=""),
    )

    resolved = cli_clients._resolve_client_executable("astron", _standalone_astron_config(), None)

    assert resolved == str(cmd_shim)
    assert resolved != str(posix_shim)


def test_explicit_client_cmd_rejects_non_executable_windows_suffix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    invalid = tmp_path / "astron-code.txt"
    invalid.write_text("not an executable", encoding="utf-8")
    monkeypatch.setattr(cli_clients.sys, "platform", "win32")

    assert cli_clients._resolve_client_executable("astron", _standalone_astron_config(), str(invalid)) is None


def test_explicit_client_cmd_requires_absolute_executable_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = _standalone_astron_config()
    relative = Path("relative-astron-code")
    non_executable = tmp_path / "astron-code"
    non_executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable = _make_executable(tmp_path / "Astron Code" / "astron-code")
    monkeypatch.chdir(tmp_path)

    assert cli_clients._resolve_client_executable("astron", cfg, str(relative)) is None
    assert cli_clients._resolve_client_executable("astron", cfg, str(non_executable)) is None
    assert cli_clients._resolve_client_executable("astron", cfg, str(executable)) == str(executable)


def test_probe_astron_version_uses_bounded_no_shell_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="astron-code 0.1.0\n", stderr="")

    monkeypatch.setattr(cli_clients.subprocess, "run", fake_run)

    assert cli_clients._probe_astron_version("/opt/astron-code") == "astron-code 0.1.0"
    assert calls[0][0] == ["/opt/astron-code", "--version"]
    assert calls[0][1]["timeout"] <= 5
    assert calls[0][1]["check"] is False
    assert "shell" not in calls[0][1]


def test_probe_astron_version_handles_failure_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli_clients.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr="failed"),
    )
    assert cli_clients._probe_astron_version("/opt/astron-code") is None

    def timeout(command: list[str], **kwargs: object) -> SimpleNamespace:
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(cli_clients.subprocess, "run", timeout)
    assert cli_clients._probe_astron_version("/opt/astron-code") is None


def test_probe_astron_version_uses_cmd_for_windows_shim_with_spaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="astron-code 0.1.0\n", stderr="")

    monkeypatch.setattr(cli_clients.sys, "platform", "win32")
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")
    monkeypatch.setattr(cli_clients.subprocess, "run", fake_run)

    executable = r"C:\Program Files\Astron Code\astron-code.cmd"
    assert cli_clients._probe_astron_version(executable) == "astron-code 0.1.0"
    assert calls == [
        [
            r"C:\Windows\System32\cmd.exe",
            "/d",
            "/s",
            "/c",
            subprocess.list2cmdline([executable, "--version"]),
        ]
    ]


@pytest.mark.asyncio
async def test_run_astron_uses_forward_proxy_env_and_preserves_product_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    executable = _make_executable(tmp_path / "Astron Code" / "astron-code")
    ca_path = tmp_path / "ca.pem"
    ca_path.write_text("test ca", encoding="utf-8")

    async def fake_create_subprocess_exec(*command: str, **kwargs: object) -> _DummyProc:
        captured["command"] = command
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.setenv("OPENAI_BASE_URL", "https://product-provider.example/v1")
    monkeypatch.setattr(cli_clients, "_probe_astron_version", lambda _command: "astron-code test", raising=False)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(
        43123,
        ["exec", "hello"],
        client="astron",
        proxy_mode="forward",
        ca_cert_path=ca_path,
        client_cmd=str(executable),
    )

    env = captured["env"]
    assert code == 0
    assert captured["command"] == (str(executable), "exec", "hello")
    assert env["HTTP_PROXY"] == "http://127.0.0.1:43123"
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:43123"
    assert env["ALL_PROXY"] == "http://127.0.0.1:43123"
    assert env["SSL_CERT_FILE"] == str(ca_path)
    assert env["CODEX_CA_CERTIFICATE"] == str(ca_path)
    assert env["OPENAI_BASE_URL"] == "https://product-provider.example/v1"
    assert "model_provider" not in captured["command"]
    assert os.path.basename(captured["command"][0]) == "astron-code"


@pytest.mark.asyncio
async def test_invalid_explicit_astron_path_fails_without_discovery_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    invalid = tmp_path / "not-executable"
    invalid.write_text("#!/bin/sh\n", encoding="utf-8")

    def fail_which(command: str) -> str | None:
        raise AssertionError(f"discovery fallback must not run for explicit path: {command}")

    async def fail_create_subprocess_exec(*_command: str, **_kwargs: object) -> _DummyProc:
        raise AssertionError("invalid explicit path must not launch a process")

    monkeypatch.setattr(cli_clients.shutil, "which", fail_which)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail_create_subprocess_exec)

    code = await run_client(
        43123,
        [],
        client="astron",
        proxy_mode="forward",
        client_cmd=str(invalid),
    )

    assert code == 1
    output = capsys.readouterr().out
    assert "is not an absolute executable file" in output
    assert "--tap-client-cmd" in output
