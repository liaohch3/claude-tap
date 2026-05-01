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


def test_opencode_registered_in_client_configs() -> None:
    cfg = CLIENT_CONFIGS["opencode"]
    assert cfg.cmd == "opencode"
    assert cfg.default_target == "https://api.anthropic.com"
    assert cfg.base_url_env == "ANTHROPIC_BASE_URL"
    # opencode is multi-provider; forward proxy is the natural default
    assert cfg.default_proxy_mode == "forward"


def test_parse_args_opencode_defaults_to_forward_mode() -> None:
    args = parse_args(["--tap-client", "opencode"])
    assert args.client == "opencode"
    assert args.proxy_mode == "forward"


def test_parse_args_opencode_explicit_reverse_overrides_default() -> None:
    args = parse_args(["--tap-client", "opencode", "--tap-proxy-mode", "reverse"])
    assert args.proxy_mode == "reverse"


def test_parse_args_claude_default_proxy_mode_unchanged() -> None:
    # Regression: changing the default-mode plumbing must not affect claude
    args = parse_args([])
    assert args.client == "claude"
    assert args.proxy_mode == "reverse"


def test_parse_args_codex_default_proxy_mode_unchanged() -> None:
    # Regression: changing the default-mode plumbing must not affect codex
    args = parse_args(["--tap-client", "codex"])
    assert args.client == "codex"
    assert args.proxy_mode == "reverse"


@pytest.mark.asyncio
async def test_run_client_opencode_forward_sets_node_ca_env(monkeypatch) -> None:
    captured: dict[str, object] = {}
    ca_path = Path("/tmp/test-ca.pem")

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/opencode")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(43123, ["run", "hello"], client="opencode", proxy_mode="forward", ca_cert_path=ca_path)

    assert code == 0
    env = captured["env"]
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:43123"
    assert env["NODE_EXTRA_CA_CERTS"] == str(ca_path)
    assert env["SSL_CERT_FILE"] == str(ca_path)


@pytest.mark.asyncio
async def test_run_client_opencode_reverse_sets_anthropic_base_url(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/opencode")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(43123, ["run", "hello"], client="opencode", proxy_mode="reverse")

    assert code == 0
    assert captured["env"]["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:43123"
    # Reverse mode for opencode must not inject a codex-only -c flag
    assert captured["cmd"] == ("/tmp/opencode", "run", "hello")
