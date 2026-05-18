from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from claude_tap import parse_args
from claude_tap.cli import CLIENT_CONFIGS, ClientConfig, run_client

SUPPORTED_CLIENTS = {
    "agy",
    "claude",
    "codex",
    "gemini",
    "kimi",
    "opencode",
    "pi",
    "hermes",
    "cursor",
    "qoder",
    "codebuddy",
}

SINGLE_REVERSE_ENV_CLIENTS = SUPPORTED_CLIENTS - {"gemini"}

SUPPORTED_DEFAULT_PROXY_MODES = {
    "agy": "forward",
    "claude": "reverse",
    "codex": "reverse",
    "gemini": "forward",
    "kimi": "reverse",
    "opencode": "forward",
    "pi": "forward",
    "hermes": "forward",
    "cursor": "forward",
    "qoder": "forward",
    "codebuddy": "reverse",
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


def test_client_matrix_contains_only_supported_clients() -> None:
    assert set(CLIENT_CONFIGS) == SUPPORTED_CLIENTS


def test_only_agy_auto_trusts_ca_on_macos() -> None:
    auto_trust_clients = {client for client, cfg in CLIENT_CONFIGS.items() if cfg.auto_trust_ca_macos}

    assert auto_trust_clients == {"agy"}


@pytest.mark.parametrize("client", sorted(SUPPORTED_CLIENTS))
def test_supported_client_default_proxy_modes_are_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    client: str,
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))

    args = parse_args(["--tap-client", client])

    assert args.client == client
    assert args.proxy_mode == SUPPORTED_DEFAULT_PROXY_MODES[client]


@pytest.mark.parametrize("client", sorted(SINGLE_REVERSE_ENV_CLIENTS))
def test_single_env_clients_keep_single_reverse_base_url_env(client: str) -> None:
    cfg = CLIENT_CONFIGS[client]

    assert cfg.reverse_base_url_envs == (cfg.base_url_env,)


def test_gemini_declares_both_reverse_base_url_envs() -> None:
    cfg = CLIENT_CONFIGS["gemini"]

    assert cfg.reverse_base_url_envs == ("GOOGLE_GEMINI_BASE_URL", "GOOGLE_VERTEX_BASE_URL")
    assert cfg.reverse_base_url_env_map(43123) == {
        "GOOGLE_GEMINI_BASE_URL": "http://127.0.0.1:43123",
        "GOOGLE_VERTEX_BASE_URL": "http://127.0.0.1:43123",
    }


def test_agy_declares_cloud_code_bridge_env() -> None:
    cfg = CLIENT_CONFIGS["agy"]

    assert cfg.base_url_env == "CLOUD_CODE_URL"
    assert cfg.default_target == "https://daily-cloudcode-pa.googleapis.com"
    assert cfg.forward_base_url_envs == ("CLOUD_CODE_URL",)
    assert cfg.forward_base_url_allowed_path_prefixes == ("/v1internal",)


def test_reverse_base_url_envs_deduplicate_primary_and_extra_envs() -> None:
    cfg = ClientConfig(
        cmd="multi-cli",
        label="Multi CLI",
        install_url="https://example.com",
        base_url_env="PRIMARY_BASE_URL",
        extra_base_url_envs=("SECONDARY_BASE_URL", "PRIMARY_BASE_URL"),
        base_url_suffix="/v1",
        default_target="https://example.com",
    )

    assert cfg.reverse_base_url_envs == ("PRIMARY_BASE_URL", "SECONDARY_BASE_URL")
    assert cfg.reverse_base_url_env_map(43123) == {
        "PRIMARY_BASE_URL": "http://127.0.0.1:43123/v1",
        "SECONDARY_BASE_URL": "http://127.0.0.1:43123/v1",
    }


@pytest.mark.asyncio
async def test_run_client_reverse_sets_all_base_url_envs_and_settings(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg = ClientConfig(
        cmd="multi-cli",
        label="Multi CLI",
        install_url="https://example.com",
        base_url_env="PRIMARY_BASE_URL",
        extra_base_url_envs=("SECONDARY_BASE_URL",),
        base_url_suffix="/v1",
        default_target="https://example.com",
        inject_settings_env=True,
    )
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.setitem(CLIENT_CONFIGS, "multi-env", cfg)
    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda name: f"/tmp/{name}")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(43123, ["--flag"], client="multi-env", proxy_mode="reverse")

    assert code == 0
    base_url = "http://127.0.0.1:43123/v1"
    env = captured["env"]
    assert env["PRIMARY_BASE_URL"] == base_url
    assert env["SECONDARY_BASE_URL"] == base_url

    cmd = captured["cmd"]
    assert cmd[:3] == (
        "/tmp/multi-cli",
        "--settings",
        json.dumps({"env": cfg.reverse_base_url_env_map(43123)}, separators=(",", ":")),
    )
    assert cmd[3:] == ("--flag",)

    out = capsys.readouterr().out
    assert out.count("PRIMARY_BASE_URL=http://127.0.0.1:43123/v1") == 1
    assert out.count("SECONDARY_BASE_URL=http://127.0.0.1:43123/v1") == 1


@pytest.mark.asyncio
async def test_run_client_agy_forward_sets_proxy_ca_and_cloud_code_url(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda name: f"/tmp/{name}")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(
        43123,
        ["--print", "ok"],
        client="agy",
        proxy_mode="forward",
        ca_cert_path=Path("/tmp/claude-tap-ca.pem"),
    )

    assert code == 0
    env = captured["env"]
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:43123"
    assert env["CLOUD_CODE_URL"] == "http://127.0.0.1:43123"
    assert "AGY_BASE_URL" not in env
    assert captured["cmd"] == ("/tmp/agy", "--print", "ok")

    out = capsys.readouterr().out
    assert "HTTPS_PROXY=http://127.0.0.1:43123" in out
    assert "CLOUD_CODE_URL=http://127.0.0.1:43123" in out
