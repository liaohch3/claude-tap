from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from claude_tap import parse_args
from claude_tap.cli import CLIENT_CONFIGS, run_client


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


def test_mimocode_registered_in_client_configs() -> None:
    cfg = CLIENT_CONFIGS["mimo"]
    assert cfg.cmd == "mimo"
    assert cfg.label == "MiMo Code"
    assert cfg.default_target == "https://api.anthropic.com"
    assert cfg.base_url_env == "ANTHROPIC_BASE_URL"
    # mimocode is an OpenCode fork; forward proxy is the natural default
    assert cfg.default_proxy_mode == "forward"


def test_parse_args_mimocode_defaults_to_forward_mode() -> None:
    args = parse_args(["--tap-client", "mimo"])
    assert args.client == "mimo"
    assert args.proxy_mode == "forward"


def test_parse_args_mimocode_explicit_reverse_overrides_default() -> None:
    args = parse_args(["--tap-client", "mimo", "--tap-proxy-mode", "reverse"])
    assert args.proxy_mode == "reverse"


@pytest.mark.asyncio
async def test_run_client_mimocode_forward_sets_node_ca_env(monkeypatch) -> None:
    captured: dict[str, object] = {}
    ca_path = Path("/tmp/test-ca.pem")

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/mimo")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(43123, ["--print", "hello"], client="mimo", proxy_mode="forward", ca_cert_path=ca_path)

    assert code == 0
    env = captured["env"]
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:43123"
    assert env["NODE_EXTRA_CA_CERTS"] == str(ca_path)
    assert env["SSL_CERT_FILE"] == str(ca_path)


@pytest.mark.asyncio
async def test_run_client_mimocode_reverse_sets_anthropic_base_url(monkeypatch) -> None:
    captured: dict[str, object] = {}
    for key in ("OPENAI_BASE_URL", "GOOGLE_GEMINI_BASE_URL"):
        monkeypatch.delenv(key, raising=False)

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/mimo")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(43123, ["--print", "hello"], client="mimo", proxy_mode="reverse")

    assert code == 0
    env = captured["env"]
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:43123"
    assert "OPENAI_BASE_URL" not in env
    assert "GOOGLE_GEMINI_BASE_URL" not in env
    assert env["MIMOCODE_MIMO_ONLY"] == "false"
    assert "localhost" in env["NO_PROXY"].split(",")
    assert captured["cmd"] == ("/tmp/mimo", "--print", "hello")


@pytest.mark.asyncio
async def test_run_client_mimocode_reverse_extends_no_proxy_for_localhost(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
    monkeypatch.setenv("NO_PROXY", "corp.example")

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/mimo")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(43123, ["--print", "hello"], client="mimo", proxy_mode="reverse")

    assert code == 0
    env = captured["env"]
    no_proxy = env["NO_PROXY"].split(",")
    assert "corp.example" in no_proxy
    assert "localhost" in no_proxy
    assert "127.0.0.1" in no_proxy
    assert "::1" in no_proxy
    assert env["no_proxy"] == env["NO_PROXY"]


@pytest.mark.asyncio
async def test_run_client_mimocode_capture_only_reverse_sets_multi_provider_urls(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/mimo")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(43123, ["--print", "hello"], client="mimo", proxy_mode="reverse", capture_only=True)

    assert code == 0
    env = captured["env"]
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:43123"
    assert env["OPENAI_BASE_URL"] == "http://127.0.0.1:43123/v1"
    assert env["GOOGLE_GEMINI_BASE_URL"] == "http://127.0.0.1:43123"
    assert env["MIMOCODE_MIMO_ONLY"] == "false"
    assert "localhost" in env["NO_PROXY"].split(",")
    assert captured["cmd"] == ("/tmp/mimo", "--print", "hello")
