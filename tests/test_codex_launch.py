from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from claude_tap.cli import _has_config_override, _reverse_proxy_trace_options, parse_args, run_client


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
async def test_run_client_codex_reverse_injects_openai_base_url(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/codex")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(43123, ["exec", "hello"], client="codex", proxy_mode="reverse")

    assert code == 0
    assert captured["cmd"] == (
        "/tmp/codex",
        "-c",
        'openai_base_url="http://127.0.0.1:43123/v1"',
        "exec",
        "hello",
    )
    assert captured["env"]["OPENAI_BASE_URL"] == "http://127.0.0.1:43123/v1"


@pytest.mark.asyncio
async def test_run_client_codex_reverse_respects_existing_openai_base_override(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        return _DummyProc()

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/codex")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(
        43123,
        ["-c", 'openai_base_url="http://example.invalid/v1"', "exec", "hello"],
        client="codex",
        proxy_mode="reverse",
    )

    assert code == 0
    assert captured["cmd"] == (
        "/tmp/codex",
        "-c",
        'openai_base_url="http://example.invalid/v1"',
        "exec",
        "hello",
    )


@pytest.mark.asyncio
async def test_run_client_codex_forward_sets_rust_tls_ca_env(monkeypatch) -> None:
    captured: dict[str, object] = {}
    ca_path = Path("/tmp/test-ca.pem")

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/codex")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(43123, ["exec", "hello"], client="codex", proxy_mode="forward", ca_cert_path=ca_path)

    assert code == 0
    assert captured["env"]["HTTPS_PROXY"] == "http://127.0.0.1:43123"
    assert captured["env"]["SSL_CERT_FILE"] == str(ca_path)
    assert captured["env"]["CODEX_CA_CERTIFICATE"] == str(ca_path)


def test_has_config_override_detects_cli_forms() -> None:
    assert _has_config_override(["-c", 'openai_base_url="http://127.0.0.1:1/v1"'], "openai_base_url") is True
    assert _has_config_override(["--config", 'openai_base_url="http://127.0.0.1:1/v1"'], "openai_base_url") is True
    assert _has_config_override(['--config=openai_base_url="http://127.0.0.1:1/v1"'], "openai_base_url") is True
    assert _has_config_override(["exec", "hello"], "openai_base_url") is False


def test_parse_args_codex_auto_detects_chatgpt_target(monkeypatch, tmp_path) -> None:
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text('{"auth_mode":"chatgpt"}\n', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    args = parse_args(["--tap-client", "codex"])

    assert args.target == "https://chatgpt.com/backend-api/codex"


def test_parse_args_claude_uses_env_base_url(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.example.test/v1/anthropic")

    args = parse_args([])

    assert args.target == "https://gateway.example.test/v1/anthropic"


def test_parse_args_claude_uses_project_settings_base_url(monkeypatch, tmp_path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    home_settings = home / ".claude"
    project_settings = project / ".claude"
    home_settings.mkdir(parents=True)
    project_settings.mkdir(parents=True)
    (home_settings / "settings.json").write_text(
        '{"env":{"ANTHROPIC_BASE_URL":"https://global.example.test/v1/anthropic"}}\n',
        encoding="utf-8",
    )
    (project_settings / "settings.local.json").write_text(
        '{"env":{"ANTHROPIC_BASE_URL":"https://project.example.test/v1/anthropic"}}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    monkeypatch.chdir(project)

    args = parse_args([])

    assert args.target == "https://project.example.test/v1/anthropic"


def test_parse_args_claude_falls_back_to_default_target(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
    monkeypatch.chdir(tmp_path)

    args = parse_args([])

    assert args.target == "https://api.anthropic.com"


def test_codex_reverse_trace_options_allow_websocket() -> None:
    options = _reverse_proxy_trace_options("codex", "https://chatgpt.com/backend-api/codex")

    assert options == {
        "strip_path_prefix": "/v1",
        "force_http": False,
    }
