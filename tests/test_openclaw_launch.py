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


def test_openclaw_registered_in_client_configs() -> None:
    cfg = CLIENT_CONFIGS["openclaw"]
    assert cfg.cmd == "openclaw"
    assert cfg.default_target == "https://api.anthropic.com"
    assert cfg.base_url_env == "ANTHROPIC_BASE_URL"
    # openclaw is multi-provider; forward proxy is the natural default
    assert cfg.default_proxy_mode == "forward"


def test_parse_args_openclaw_defaults_to_forward_mode() -> None:
    args = parse_args(["--tap-client", "openclaw"])
    assert args.client == "openclaw"
    assert args.proxy_mode == "forward"


def test_parse_args_openclaw_explicit_reverse_overrides_default() -> None:
    args = parse_args(["--tap-client", "openclaw", "--tap-proxy-mode", "reverse"])
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
async def test_run_client_openclaw_forward_sets_node_ca_env(monkeypatch) -> None:
    captured: dict[str, object] = {}
    ca_path = Path("/tmp/test-ca.pem")

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/openclaw")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(43123, ["run", "hello"], client="openclaw", proxy_mode="forward", ca_cert_path=ca_path)

    assert code == 0
    env = captured["env"]
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:43123"
    # openclaw is a Node 22+/24 binary; needs NODE_EXTRA_CA_CERTS for TLS trust
    assert env["NODE_EXTRA_CA_CERTS"] == str(ca_path)


@pytest.mark.asyncio
async def test_run_client_openclaw_reverse_sets_anthropic_base_url(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/openclaw")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(43123, ["run", "hello"], client="openclaw", proxy_mode="reverse")

    assert code == 0
    assert captured["env"]["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:43123"
    # Reverse mode for openclaw must not inject a codex-only -c flag
    assert captured["cmd"] == ("/tmp/openclaw", "run", "hello")


# ---------------------------------------------------------------------------
# argv rewrite: openclaw 2026.4+ delegates `gateway start` to launchd/systemd
# which spawns the gateway in a fresh env, breaking HTTPS_PROXY inheritance.
# We rewrite to `gateway run` (foreground) so the spawned process is our child
# and inherits the injected env.
# ---------------------------------------------------------------------------


async def _capture_cmd(monkeypatch) -> dict[str, object]:
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        return _DummyProc()

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/openclaw")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    return captured


@pytest.mark.asyncio
async def test_run_client_openclaw_rewrites_gateway_start_to_gateway_run(monkeypatch) -> None:
    captured = await _capture_cmd(monkeypatch)
    code = await run_client(43123, ["gateway", "start"], client="openclaw", proxy_mode="forward")
    assert code == 0
    assert captured["cmd"] == ("/tmp/openclaw", "gateway", "run")


@pytest.mark.asyncio
async def test_run_client_openclaw_rewrite_preserves_trailing_flags(monkeypatch) -> None:
    captured = await _capture_cmd(monkeypatch)
    code = await run_client(
        43123,
        ["gateway", "start", "--allow-unconfigured", "--port", "18789"],
        client="openclaw",
        proxy_mode="forward",
    )
    assert code == 0
    assert captured["cmd"] == (
        "/tmp/openclaw",
        "gateway",
        "run",
        "--allow-unconfigured",
        "--port",
        "18789",
    )


@pytest.mark.asyncio
async def test_run_client_openclaw_gateway_run_passthrough_unchanged(monkeypatch) -> None:
    # Already-foreground invocations must not be touched
    captured = await _capture_cmd(monkeypatch)
    code = await run_client(43123, ["gateway", "run"], client="openclaw", proxy_mode="forward")
    assert code == 0
    assert captured["cmd"] == ("/tmp/openclaw", "gateway", "run")


@pytest.mark.asyncio
async def test_run_client_openclaw_other_subcommands_unchanged(monkeypatch) -> None:
    captured = await _capture_cmd(monkeypatch)
    code = await run_client(43123, ["tui"], client="openclaw", proxy_mode="forward")
    assert code == 0
    assert captured["cmd"] == ("/tmp/openclaw", "tui")


@pytest.mark.asyncio
async def test_run_client_openclaw_agent_local_unchanged(monkeypatch) -> None:
    captured = await _capture_cmd(monkeypatch)
    code = await run_client(
        43123,
        ["agent", "--local", "--agent", "main", "--message", "hi"],
        client="openclaw",
        proxy_mode="forward",
    )
    assert code == 0
    assert captured["cmd"] == (
        "/tmp/openclaw",
        "agent",
        "--local",
        "--agent",
        "main",
        "--message",
        "hi",
    )


@pytest.mark.asyncio
async def test_run_client_codex_not_affected_by_openclaw_rewrite(monkeypatch) -> None:
    # The rewrite must only fire for openclaw — codex `gateway start` (hypothetical) stays raw
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        return _DummyProc()

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/codex")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(43123, ["gateway", "start"], client="codex", proxy_mode="forward")
    assert code == 0
    assert captured["cmd"] == ("/tmp/codex", "gateway", "start")
