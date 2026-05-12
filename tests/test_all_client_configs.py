from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from claude_tap.cli import CLIENT_CONFIGS, parse_args, run_client

REQUESTED_CLIENTS = {
    "claude",
    "codex",
    "gemini",
    "opencode",
    "pi",
    "kimi",
    "iflow",
    "cursor",
    "qoder",
    "devin",
    "hermes",
}

EXPECTED_COMMANDS = {
    "claude": "claude",
    "codex": "codex",
    "gemini": "gemini",
    "opencode": "opencode",
    "pi": "pi",
    "kimi": "kimi",
    "iflow": "iflow",
    "cursor": "cursor-agent",
    "qoder": "qodercli",
    "devin": "devin",
    "hermes": "hermes",
}

EXPECTED_DEFAULT_PROXY_MODES = {
    "claude": "reverse",
    "codex": "reverse",
    "gemini": "forward",
    "opencode": "forward",
    "pi": "forward",
    "kimi": "reverse",
    "iflow": "reverse",
    "cursor": "forward",
    "qoder": "forward",
    "devin": "forward",
    "hermes": "forward",
}


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


def test_all_requested_clients_are_registered() -> None:
    assert REQUESTED_CLIENTS <= CLIENT_CONFIGS.keys()
    assert set(EXPECTED_COMMANDS) == REQUESTED_CLIENTS
    assert set(EXPECTED_DEFAULT_PROXY_MODES) == REQUESTED_CLIENTS


@pytest.mark.parametrize("client", sorted(REQUESTED_CLIENTS))
def test_client_binary_names_match_official_install_packages(client: str) -> None:
    assert CLIENT_CONFIGS[client].cmd == EXPECTED_COMMANDS[client]


@pytest.mark.parametrize("client", sorted(REQUESTED_CLIENTS))
def test_parse_args_accepts_requested_clients_and_resolves_default_modes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    client: str,
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))

    args = parse_args(["--tap-client", client])

    assert args.client == client
    assert args.proxy_mode == EXPECTED_DEFAULT_PROXY_MODES[client]
    assert args.target == CLIENT_CONFIGS[client].default_target


@pytest.mark.parametrize("client", sorted(REQUESTED_CLIENTS))
@pytest.mark.asyncio
async def test_run_client_reverse_sets_all_configured_base_url_envs(
    monkeypatch: pytest.MonkeyPatch,
    client: str,
) -> None:
    cfg = CLIENT_CONFIGS[client]
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _DummyProc()

    for env_key in cfg.reverse_base_url_envs:
        monkeypatch.delenv(env_key, raising=False)
    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda name: f"/tmp/{name}")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(43123, ["--version"], client=client, proxy_mode="reverse")

    assert code == 0
    assert captured["cmd"][0] == f"/tmp/{cfg.cmd}"
    env = captured["env"]
    for env_key in cfg.reverse_base_url_envs:
        assert env[env_key] == cfg.reverse_base_url(43123)


@pytest.mark.parametrize("client", sorted(REQUESTED_CLIENTS))
@pytest.mark.asyncio
async def test_run_client_forward_sets_proxy_and_generic_ca_envs(
    monkeypatch: pytest.MonkeyPatch,
    client: str,
) -> None:
    cfg = CLIENT_CONFIGS[client]
    captured: dict[str, object] = {}
    ca_path = Path("/tmp/test-ca.pem")

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _DummyProc()

    for env_key in cfg.reverse_base_url_envs:
        monkeypatch.delenv(env_key, raising=False)
    monkeypatch.setenv("NO_PROXY", "example.com")
    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda name: f"/tmp/{name}")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(43123, ["--version"], client=client, proxy_mode="forward", ca_cert_path=ca_path)

    assert code == 0
    assert captured["cmd"][0] == f"/tmp/{cfg.cmd}"
    env = captured["env"]
    assert env["HTTP_PROXY"] == "http://127.0.0.1:43123"
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:43123"
    assert env["ALL_PROXY"] == "http://127.0.0.1:43123"
    assert env["http_proxy"] == "http://127.0.0.1:43123"
    assert env["https_proxy"] == "http://127.0.0.1:43123"
    assert env["all_proxy"] == "http://127.0.0.1:43123"
    assert env["NODE_EXTRA_CA_CERTS"] == str(ca_path)
    assert env["SSL_CERT_FILE"] == str(ca_path)
    assert env["CODEX_CA_CERTIFICATE"] == str(ca_path)
    assert env["REQUESTS_CA_BUNDLE"] == str(ca_path)
    assert env["NO_PROXY"] == env["no_proxy"]
    for bypass in ("example.com", "localhost", "127.0.0.1", "::1"):
        assert bypass in env["NO_PROXY"]
    for env_key in cfg.reverse_base_url_envs:
        assert env_key not in env


def test_multi_base_url_clients_expose_all_reverse_envs() -> None:
    assert CLIENT_CONFIGS["gemini"].reverse_base_url_envs == (
        "GOOGLE_GEMINI_BASE_URL",
        "GOOGLE_VERTEX_BASE_URL",
    )
    assert CLIENT_CONFIGS["iflow"].reverse_base_url_envs == ("IFLOW_baseUrl", "IFLOW_BASE_URL")
