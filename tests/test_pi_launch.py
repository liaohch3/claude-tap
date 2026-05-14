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


def test_pi_registered_in_client_configs() -> None:
    cfg = CLIENT_CONFIGS["pi"]

    assert cfg.cmd == "pi"
    assert cfg.label == "Pi"
    assert cfg.default_target == "https://api.openai.com"
    assert cfg.base_url_env == "OPENAI_BASE_URL"
    assert cfg.base_url_suffix == "/v1"
    assert cfg.default_proxy_mode == "forward"


def test_parse_args_pi_defaults_to_forward_mode() -> None:
    args = parse_args(["--tap-client", "pi"])

    assert args.client == "pi"
    assert args.target == "https://api.openai.com"
    assert args.proxy_mode == "forward"


def test_parse_args_pi_explicit_reverse_overrides_default() -> None:
    args = parse_args(["--tap-client", "pi", "--tap-proxy-mode", "reverse"])

    assert args.client == "pi"
    assert args.proxy_mode == "reverse"


@pytest.mark.asyncio
async def test_run_client_pi_forward_sets_proxy_ca_and_preserves_args(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    ca_path = Path("/tmp/test-ca.pem")

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("NO_PROXY", "example.com")
    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/pi")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(
        43123,
        ["--model", "openai-codex/gpt-5.3-codex-spark", "-p", "hello"],
        client="pi",
        proxy_mode="forward",
        ca_cert_path=ca_path,
    )

    assert code == 0
    assert captured["cmd"] == (
        "/tmp/pi",
        "--model",
        "openai-codex/gpt-5.3-codex-spark",
        "-p",
        "hello",
    )
    env = captured["env"]
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:43123"
    assert env["http_proxy"] == "http://127.0.0.1:43123"
    assert env["NODE_EXTRA_CA_CERTS"] == str(ca_path)
    assert env["SSL_CERT_FILE"] == str(ca_path)
    assert "example.com" in env["NO_PROXY"]
    assert "localhost" in env["NO_PROXY"]
    assert "127.0.0.1" in env["NO_PROXY"]
    assert env["no_proxy"] == env["NO_PROXY"]
    assert "OPENAI_BASE_URL" not in env


@pytest.mark.asyncio
async def test_run_client_pi_reverse_sets_openai_base_url_without_codex_config(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/pi")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(43123, ["--provider", "openai", "-p", "hello"], client="pi", proxy_mode="reverse")

    assert code == 0
    assert captured["cmd"] == ("/tmp/pi", "--provider", "openai", "-p", "hello")
    assert captured["env"]["OPENAI_BASE_URL"] == "http://127.0.0.1:43123/v1"
