from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from claude_tap import parse_args
from claude_tap.cli import CLIENT_CONFIGS, ClientConfig, run_client


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


def test_client_config_default_proxy_mode_defaults_to_reverse() -> None:
    cfg = ClientConfig(
        cmd="x",
        label="X",
        install_url="https://example.com",
        base_url_env="X_BASE_URL",
        base_url_suffix="",
        default_target="https://example.com",
    )
    assert cfg.default_proxy_mode == "reverse"


def test_hermes_registered_in_client_configs() -> None:
    cfg = CLIENT_CONFIGS["hermes"]
    assert cfg.cmd == "hermes"
    assert cfg.label == "Hermes Agent"
    assert cfg.default_target == "https://api.openai.com"
    assert cfg.base_url_env == "OPENAI_BASE_URL"
    assert cfg.base_url_suffix == "/v1"
    assert cfg.default_proxy_mode == "forward"


def test_claude_default_proxy_mode_unchanged() -> None:
    assert CLIENT_CONFIGS["claude"].default_proxy_mode == "reverse"


def test_codex_default_proxy_mode_unchanged() -> None:
    assert CLIENT_CONFIGS["codex"].default_proxy_mode == "reverse"


def test_parse_args_hermes_defaults_to_forward_mode() -> None:
    args = parse_args(["--tap-client", "hermes"])
    assert args.client == "hermes"
    assert args.proxy_mode == "forward"


def test_parse_args_hermes_explicit_reverse_overrides_default() -> None:
    args = parse_args(["--tap-client", "hermes", "--tap-proxy-mode", "reverse"])
    assert args.client == "hermes"
    assert args.proxy_mode == "reverse"


def test_parse_args_claude_default_unchanged() -> None:
    args = parse_args([])
    assert args.client == "claude"
    assert args.proxy_mode == "reverse"


def test_parse_args_codex_default_unchanged() -> None:
    args = parse_args(["--tap-client", "codex"])
    assert args.client == "codex"
    assert args.proxy_mode == "reverse"


@pytest.mark.asyncio
async def test_run_client_hermes_forward_sets_python_ca_env(monkeypatch) -> None:
    captured: dict[str, object] = {}
    ca_path = Path("/tmp/test-ca.pem")

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/hermes")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(43123, ["chat"], client="hermes", proxy_mode="forward", ca_cert_path=ca_path)

    assert code == 0
    env = captured["env"]
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:43123"
    # hermes uses httpx/requests; both honor SSL_CERT_FILE; requests also reads REQUESTS_CA_BUNDLE
    assert env["SSL_CERT_FILE"] == str(ca_path)
    assert env["REQUESTS_CA_BUNDLE"] == str(ca_path)


@pytest.mark.asyncio
async def test_run_client_codex_forward_still_sets_existing_ca_env(monkeypatch) -> None:
    """Regression: codex still gets SSL_CERT_FILE and CODEX_CA_CERTIFICATE."""
    captured: dict[str, object] = {}
    ca_path = Path("/tmp/test-ca.pem")

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/codex")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(43123, ["exec", "hi"], client="codex", proxy_mode="forward", ca_cert_path=ca_path)

    assert code == 0
    env = captured["env"]
    assert env["SSL_CERT_FILE"] == str(ca_path)
    assert env["CODEX_CA_CERTIFICATE"] == str(ca_path)


# ---------------------------------------------------------------------------
# argv rewrite: hermes recent versions delegate `gateway start` to launchd /
# systemd, which spawns the gateway in a fresh env that does NOT inherit
# HTTPS_PROXY / CA. We rewrite to `gateway run` (foreground) so the spawned
# process is our child and inherits the injected env.
# ---------------------------------------------------------------------------


async def _capture_cmd(monkeypatch, which: str = "/tmp/hermes") -> dict[str, object]:
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        return _DummyProc()

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: which)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    return captured


@pytest.mark.asyncio
async def test_run_client_hermes_rewrites_gateway_start_to_gateway_run(monkeypatch) -> None:
    captured = await _capture_cmd(monkeypatch)
    code = await run_client(43123, ["gateway", "start"], client="hermes", proxy_mode="forward")
    assert code == 0
    assert captured["cmd"] == ("/tmp/hermes", "gateway", "run")


@pytest.mark.asyncio
async def test_run_client_hermes_rewrite_preserves_trailing_flags(monkeypatch) -> None:
    captured = await _capture_cmd(monkeypatch)
    code = await run_client(
        43123,
        ["gateway", "start", "--profile", "coder", "--replace"],
        client="hermes",
        proxy_mode="forward",
    )
    assert code == 0
    assert captured["cmd"] == (
        "/tmp/hermes",
        "gateway",
        "run",
        "--profile",
        "coder",
        "--replace",
    )


@pytest.mark.asyncio
async def test_run_client_hermes_gateway_run_passthrough_unchanged(monkeypatch) -> None:
    captured = await _capture_cmd(monkeypatch)
    code = await run_client(43123, ["gateway", "run"], client="hermes", proxy_mode="forward")
    assert code == 0
    assert captured["cmd"] == ("/tmp/hermes", "gateway", "run")


@pytest.mark.asyncio
async def test_run_client_hermes_other_subcommands_unchanged(monkeypatch) -> None:
    captured = await _capture_cmd(monkeypatch)
    code = await run_client(43123, ["chat"], client="hermes", proxy_mode="forward")
    assert code == 0
    assert captured["cmd"] == ("/tmp/hermes", "chat")


@pytest.mark.asyncio
async def test_run_client_codex_not_affected_by_hermes_rewrite(monkeypatch) -> None:
    captured = await _capture_cmd(monkeypatch, which="/tmp/codex")
    code = await run_client(43123, ["gateway", "start"], client="codex", proxy_mode="forward")
    assert code == 0
    # The hermes rewrite must not fire for codex
    assert captured["cmd"] == ("/tmp/codex", "gateway", "start")


@pytest.mark.asyncio
async def test_run_client_hermes_reverse_sets_openai_base_url(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/hermes")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(43123, ["chat"], client="hermes", proxy_mode="reverse")

    assert code == 0
    assert captured["env"]["OPENAI_BASE_URL"] == "http://127.0.0.1:43123/v1"
    # Reverse mode for hermes must not inject the codex-only -c flag
    assert captured["cmd"] == ("/tmp/hermes", "chat")
