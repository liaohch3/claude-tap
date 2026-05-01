from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from claude_tap.cli import _has_config_override, parse_args, run_client


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


@pytest.mark.asyncio
async def test_run_client_copilot_forward_sets_node_ca_env(monkeypatch) -> None:
    captured: dict[str, object] = {}
    ca_path = Path("/tmp/test-ca.pem")

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/copilot")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(43123, ["-p", "hello"], client="copilot", proxy_mode="forward", ca_cert_path=ca_path)

    assert code == 0
    assert captured["cmd"] == ("/tmp/copilot", "-p", "hello")
    assert captured["env"]["HTTPS_PROXY"] == "http://127.0.0.1:43123"
    assert captured["env"]["NODE_EXTRA_CA_CERTS"] == str(ca_path)


@pytest.mark.asyncio
async def test_run_client_copilot_reverse_is_rejected(monkeypatch) -> None:
    async def fake_create_subprocess_exec(*cmd, **kwargs):
        raise AssertionError("Copilot reverse mode should fail before spawning")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    code = await run_client(43123, [], client="copilot", proxy_mode="reverse")

    assert code == 1


def test_parse_args_copilot_defaults_to_forward() -> None:
    args = parse_args(["--tap-client", "copilot"])

    assert args.client == "copilot"
    assert args.target == "https://api.githubcopilot.com"
    assert args.proxy_mode == "forward"


def test_parse_args_copilot_respects_explicit_proxy_mode() -> None:
    args = parse_args(["--tap-client", "copilot", "--tap-proxy-mode", "reverse"])

    assert args.client == "copilot"
    assert args.proxy_mode == "reverse"


def test_has_config_override_detects_cli_forms() -> None:
    assert _has_config_override(["-c", 'openai_base_url="http://127.0.0.1:1/v1"'], "openai_base_url") is True
    assert _has_config_override(["--config", 'openai_base_url="http://127.0.0.1:1/v1"'], "openai_base_url") is True
    assert _has_config_override(['--config=openai_base_url="http://127.0.0.1:1/v1"'], "openai_base_url") is True
    assert _has_config_override(["exec", "hello"], "openai_base_url") is False
