from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from claude_tap import cli_clients, parse_args
from claude_tap.cli import CLIENT_CONFIGS, ClientConfig, run_client

SUPPORTED_CLIENTS = {
    "agy",
    "claude",
    "codex",
    "codexapp",
    "gemini",
    "kimi",
    "kimi-code",
    "mimo",
    "opencode",
    "openclaw",
    "pi",
    "hermes",
    "cursor",
    "qoder",
    "codebuddy",
}

SINGLE_REVERSE_ENV_CLIENTS = SUPPORTED_CLIENTS - {"claude", "gemini", "kimi-code", "openclaw", "codexapp"}

SUPPORTED_DEFAULT_PROXY_MODES = {
    "agy": "forward",
    "claude": "reverse",
    "codex": "reverse",
    "codexapp": "transcript",
    "gemini": "forward",
    "kimi": "reverse",
    "kimi-code": "reverse",
    "mimo": "forward",
    "opencode": "forward",
    "openclaw": "reverse",
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


def test_claude_declares_provider_reverse_base_url_envs() -> None:
    cfg = CLIENT_CONFIGS["claude"]

    assert cfg.reverse_base_url_envs == (
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_BEDROCK_BASE_URL",
        "ANTHROPIC_VERTEX_BASE_URL",
    )


def test_agy_declares_cloud_code_bridge_env() -> None:
    cfg = CLIENT_CONFIGS["agy"]

    assert cfg.base_url_env == "CLOUD_CODE_URL"
    assert cfg.default_target == "https://daily-cloudcode-pa.googleapis.com"
    assert cfg.forward_base_url_envs == ("CLOUD_CODE_URL",)
    assert cfg.forward_base_url_allowed_path_prefixes == ("/v1internal",)


def test_codexapp_declares_transcript_only_mode() -> None:
    cfg = CLIENT_CONFIGS["codexapp"]

    assert cfg.label == "Codex App"
    assert cfg.default_target == "codex-app://sessions"
    assert cfg.default_proxy_mode == "transcript"
    assert cfg.transcript_only is True


def test_parse_args_codexapp_rejects_proxy_mode() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--tap-client", "codexapp", "--tap-proxy-mode", "forward"])


def test_parse_args_codexapp_rejects_trust_ca() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--tap-client", "codexapp", "--tap-trust-ca"])


def test_openclaw_declares_multi_provider_reverse_envs() -> None:
    cfg = CLIENT_CONFIGS["openclaw"]

    assert cfg.default_proxy_mode == "reverse"
    assert cfg.reverse_base_url_envs == (
        "OPENAI_BASE_URL",
        "ANTHROPIC_BASE_URL",
        "GOOGLE_GEMINI_BASE_URL",
        "OPENROUTER_BASE_URL",
        "CUSTOM_BASE_URL",
    )


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
async def test_run_client_openclaw_reverse_patches_temp_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = tmp_path / ".openclaw" / "openclaw.json"
    config.parent.mkdir()
    config.write_text(
        json.dumps(
            {
                "agents": {"defaults": {"model": {"primary": "phistory/phistory-dummy"}}},
                "models": {
                    "providers": {
                        "phistory": {
                            "baseUrl": "https://relay.example.com/v1",
                            "api": "openai-responses",
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        openclaw_config = Path(kwargs["env"]["OPENCLAW_CONFIG_PATH"])
        captured["config_text"] = openclaw_config.read_text(encoding="utf-8")
        return _DummyProc()

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda name: f"/tmp/{name}")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(43123, ["agent"], client="openclaw", proxy_mode="reverse")

    assert code == 0
    env = captured["env"]
    temp_config = Path(env["OPENCLAW_CONFIG_PATH"])
    assert not temp_config.exists()
    assert captured["cmd"] == ("/tmp/openclaw", "agent")
    config_text = captured["config_text"]
    assert isinstance(config_text, str)
    patched_config = json.loads(config_text)
    assert patched_config["models"]["providers"]["phistory"]["baseUrl"] == "http://127.0.0.1:43123/v1"


@pytest.mark.asyncio
async def test_run_client_openclaw_reverse_patches_model_arg_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = tmp_path / ".openclaw" / "openclaw.json"
    config.parent.mkdir()
    config.write_text(
        json.dumps(
            {
                "agents": {"defaults": {"model": {"primary": "openai-codex/gpt-5.4"}}},
                "models": {
                    "providers": {
                        "anthropic": {
                            "baseUrl": "https://api.anthropic.com",
                            "api": "anthropic-messages",
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["env"] = kwargs["env"]
        openclaw_config = Path(kwargs["env"]["OPENCLAW_CONFIG_PATH"])
        captured["config_text"] = openclaw_config.read_text(encoding="utf-8")
        return _DummyProc()

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda name: f"/tmp/{name}")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(
        43123,
        ["agent", "--model", "anthropic/claude-opus-4-6"],
        client="openclaw",
        proxy_mode="reverse",
    )

    assert code == 0
    env = captured["env"]
    assert "OPENCLAW_CONFIG_PATH" in env
    config_text = captured["config_text"]
    assert isinstance(config_text, str)
    patched_config = json.loads(config_text)
    assert patched_config["models"]["providers"]["anthropic"]["baseUrl"] == "http://127.0.0.1:43123"


@pytest.mark.asyncio
async def test_run_client_openclaw_reverse_cleans_temp_config_on_spawn_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = tmp_path / ".openclaw" / "openclaw.json"
    config.parent.mkdir()
    config.write_text(
        json.dumps(
            {
                "agents": {"defaults": {"model": {"primary": "anthropic/claude-opus-4-6"}}},
                "models": {
                    "providers": {
                        "anthropic": {
                            "baseUrl": "https://api.anthropic.com",
                            "api": "anthropic-messages",
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*_cmd, **kwargs):
        captured["config_path"] = Path(kwargs["env"]["OPENCLAW_CONFIG_PATH"])
        raise OSError("spawn failed")

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda name: f"/tmp/{name}")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    with pytest.raises(OSError, match="spawn failed"):
        await run_client(43123, ["agent"], client="openclaw", proxy_mode="reverse")

    config_path = captured["config_path"]
    assert isinstance(config_path, Path)
    assert not config_path.exists()


def test_openclaw_config_helpers_cover_paths_and_invalid_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.json"
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{", encoding="utf-8")

    assert cli_clients._read_openclaw_config(missing) is None
    assert cli_clients._read_openclaw_config(invalid) is None

    explicit = tmp_path / "explicit.json"
    monkeypatch.setenv("OPENCLAW_CONFIG_PATH", str(explicit))
    assert cli_clients._openclaw_config_path() == explicit

    monkeypatch.delenv("OPENCLAW_CONFIG_PATH")
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(tmp_path / "state"))
    assert cli_clients._openclaw_config_path() == tmp_path / "state" / "openclaw.json"


def test_openclaw_model_and_provider_helpers_cover_fallback_shapes() -> None:
    assert cli_clients._openclaw_primary_model({}) is None
    assert cli_clients._openclaw_primary_model({}, ["--model=anthropic/claude"]) == "anthropic/claude"
    assert cli_clients._openclaw_primary_model({}, ["-m", "openai/gpt-5"]) == "openai/gpt-5"
    assert cli_clients._openclaw_primary_model({"agents": {"defaults": {"model": "openai/gpt-5"}}}) == "openai/gpt-5"
    assert (
        cli_clients._openclaw_primary_model({"agents": {"defaults": {"models": {"anthropic/claude": {}}}}})
        == "anthropic/claude"
    )
    assert (
        cli_clients._openclaw_primary_model({"agents": {"defaults": {"model": {"primary": "openrouter/auto"}}}})
        == "openrouter/auto"
    )

    assert cli_clients._openclaw_provider_proxy_url({}, "http://127.0.0.1:43123") == "http://127.0.0.1:43123/v1"
    assert (
        cli_clients._openclaw_provider_proxy_url({"api": "openai-responses"}, "http://127.0.0.1:43123")
        == "http://127.0.0.1:43123/v1"
    )
    assert (
        cli_clients._openclaw_provider_proxy_url({"api": "anthropic"}, "http://127.0.0.1:43123")
        == "http://127.0.0.1:43123"
    )
    assert (
        cli_clients._openclaw_provider_target_url({"api": "openai-responses"}, "https://relay.example.com/v1/")
        == "https://relay.example.com"
    )
    assert (
        cli_clients._openclaw_provider_target_url({"api": "anthropic"}, "https://relay.example.com/anthropic/")
        == "https://relay.example.com/anthropic"
    )


def test_openclaw_config_patch_rejects_incomplete_configs() -> None:
    proxy_url = "http://127.0.0.1:43123"

    assert cli_clients._openclaw_config_with_proxy({}, proxy_url) is None
    assert cli_clients._openclaw_config_with_proxy({"agents": {"defaults": {"model": "claude"}}}, proxy_url) is None
    assert cli_clients._openclaw_config_with_proxy({"agents": {"defaults": {"model": "openai/gpt"}}}, proxy_url) is None
    assert (
        cli_clients._openclaw_config_with_proxy(
            {"agents": {"defaults": {"model": "openai/gpt"}}, "models": {"providers": {}}},
            proxy_url,
        )
        is None
    )


def test_openclaw_reverse_env_falls_back_without_patchable_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("OPENCLAW_CONFIG_PATH", str(tmp_path / "missing.json"))

    env = cli_clients._openclaw_reverse_env(43123)

    assert env == {"OPENAI_BASE_URL": "http://127.0.0.1:43123/v1"}

    assert cli_clients._openclaw_reverse_env(43123, ["--model", "anthropic/claude"]) == {
        "ANTHROPIC_BASE_URL": "http://127.0.0.1:43123"
    }

    monkeypatch.setenv("OPENAI_API_KEY", "openai-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-token")
    assert cli_clients._openclaw_reverse_env(43123) == {"OPENAI_BASE_URL": "http://127.0.0.1:43123/v1"}

    monkeypatch.delenv("OPENAI_API_KEY")
    assert cli_clients._openclaw_reverse_env(43123) == {"ANTHROPIC_BASE_URL": "http://127.0.0.1:43123"}


def test_detect_openclaw_target_uses_config_then_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = tmp_path / "openclaw.json"
    config.write_text(
        json.dumps(
            {
                "agents": {"defaults": {"model": "openai/gpt-5"}},
                "models": {"providers": {"openai": {"baseUrl": "https://relay.example.com/v1/"}}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENCLAW_CONFIG_PATH", str(config))

    assert cli_clients._detect_openclaw_target() == "https://relay.example.com"

    config.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("OPENROUTER_API_KEY", "token")

    assert cli_clients._detect_openclaw_target() == "https://openrouter.ai/api/v1"


def test_detect_openclaw_target_uses_model_arg_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = tmp_path / "openclaw.json"
    config.write_text(
        json.dumps(
            {
                "agents": {"defaults": {"model": {"primary": "openai/default"}}},
                "models": {
                    "providers": {
                        "openai": {"baseUrl": "https://openai.example.com/v1", "api": "openai-responses"},
                        "anthropic": {"baseUrl": "https://anthropic.example.com", "api": "anthropic-messages"},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENCLAW_CONFIG_PATH", str(config))

    assert cli_clients._detect_openclaw_target(["--model", "anthropic/claude-opus-4-6"]) == (
        "https://anthropic.example.com"
    )


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
