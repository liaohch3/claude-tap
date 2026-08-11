from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from claude_tap import parse_args
from claude_tap.cli import CLIENT_CONFIGS, ClientConfig, run_client
from claude_tap.cli_clients import _patch_dotenv_values, _patch_hermes_model_base_url


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
    assert cfg.default_proxy_mode == "reverse"


def test_claude_default_proxy_mode_unchanged() -> None:
    assert CLIENT_CONFIGS["claude"].default_proxy_mode == "reverse"


def test_codex_default_proxy_mode_unchanged() -> None:
    assert CLIENT_CONFIGS["codex"].default_proxy_mode == "reverse"


def test_parse_args_hermes_defaults_to_reverse_mode() -> None:
    args = parse_args(["--tap-client", "hermes"])
    assert args.client == "hermes"
    assert args.proxy_mode == "reverse"


def test_patch_hermes_model_base_url_preserves_other_config() -> None:
    source = (
        "model:\n  provider: custom\n  base_url: https://upstream.example/v1\n"
        "providers:\n  custom:\n    base_url: https://provider.example/v1\n"
        "toolsets:\n  - web\n"
    )

    patched = _patch_hermes_model_base_url(source, "http://127.0.0.1:43123/v1")

    assert "  provider: custom\n" in patched
    assert "  base_url: http://127.0.0.1:43123/v1\n" in patched
    assert "toolsets:\n  - web\n" in patched
    assert "upstream.example" not in patched
    assert "provider.example" not in patched
    assert patched.count("base_url: http://127.0.0.1:43123/v1") == 2


def test_patch_hermes_base_urls_adds_missing_model_section() -> None:
    source = "providers:\n  custom:\n    base_url: https://provider.example/v1\n"

    patched = _patch_hermes_model_base_url(source, "http://127.0.0.1:43123/v1")

    assert patched.startswith("model:\n  base_url: http://127.0.0.1:43123/v1\n")
    assert "provider.example" not in patched


def test_patch_dotenv_values_preserves_secrets_and_replaces_provider_url() -> None:
    source = "CUSTOM_API_KEY=secret\nCUSTOM_BASE_URL=https://upstream.example/v1\n# keep\n"

    patched = _patch_dotenv_values(source, {"CUSTOM_BASE_URL": "http://127.0.0.1:43123/v1"})

    assert "CUSTOM_API_KEY=secret\n" in patched
    assert "CUSTOM_BASE_URL=http://127.0.0.1:43123/v1\n" in patched
    assert "# keep\n" in patched


def test_parse_args_hermes_reverse_detects_active_model_base_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(
        "model:\n"
        "  provider: my-api\n"
        "  base_url: http://127.0.0.1:3000/v1\n"
        "providers:\n"
        "  my-api:\n"
        "    base_url: http://127.0.0.1:4000/v1\n",
        encoding="utf-8",
    )

    args = parse_args(["--tap-client", "hermes"])

    assert args.proxy_mode == "reverse"
    assert args.target == "http://127.0.0.1:3000"


def test_parse_args_hermes_explicit_target_overrides_detected_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text("model:\n  base_url: http://127.0.0.1:3000/v1\n", encoding="utf-8")

    args = parse_args(["--tap-client", "hermes", "--tap-target", "https://gateway.example/v1"])

    assert args.target == "https://gateway.example/v1"


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
async def test_run_client_hermes_rewrite_after_long_global_option(monkeypatch) -> None:
    # `hermes --profile work gateway start` is the documented shape;
    # the rewrite must skip the leading global option before matching.
    captured = await _capture_cmd(monkeypatch)
    code = await run_client(
        43123,
        ["--profile", "work", "gateway", "start"],
        client="hermes",
        proxy_mode="forward",
    )
    assert code == 0
    assert captured["cmd"] == ("/tmp/hermes", "--profile", "work", "gateway", "run")


@pytest.mark.asyncio
async def test_run_client_hermes_rewrite_after_short_profile_option(monkeypatch) -> None:
    captured = await _capture_cmd(monkeypatch)
    code = await run_client(
        43123,
        ["-p", "work", "gateway", "start"],
        client="hermes",
        proxy_mode="forward",
    )
    assert code == 0
    assert captured["cmd"] == ("/tmp/hermes", "-p", "work", "gateway", "run")


@pytest.mark.asyncio
async def test_run_client_hermes_rewrite_after_profile_equals_form(monkeypatch) -> None:
    captured = await _capture_cmd(monkeypatch)
    code = await run_client(
        43123,
        ["--profile=work", "gateway", "start"],
        client="hermes",
        proxy_mode="forward",
    )
    assert code == 0
    assert captured["cmd"] == ("/tmp/hermes", "--profile=work", "gateway", "run")


@pytest.mark.asyncio
async def test_run_client_hermes_rewrite_after_boolean_global_flag(monkeypatch) -> None:
    captured = await _capture_cmd(monkeypatch)
    code = await run_client(
        43123,
        ["--ignore-user-config", "gateway", "start"],
        client="hermes",
        proxy_mode="forward",
    )
    assert code == 0
    assert captured["cmd"] == ("/tmp/hermes", "--ignore-user-config", "gateway", "run")


@pytest.mark.asyncio
async def test_run_client_hermes_rewrite_with_global_option_and_trailing_flags(monkeypatch) -> None:
    captured = await _capture_cmd(monkeypatch)
    code = await run_client(
        43123,
        ["--profile", "work", "gateway", "start", "--replace"],
        client="hermes",
        proxy_mode="forward",
    )
    assert code == 0
    assert captured["cmd"] == ("/tmp/hermes", "--profile", "work", "gateway", "run", "--replace")


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
async def test_run_client_hermes_reverse_sets_openai_base_url(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    original_config = "model:\n  provider: custom\n  base_url: https://upstream.example/v1\n"
    (tmp_path / "config.yaml").write_text(original_config, encoding="utf-8")
    (tmp_path / ".env").write_text(
        "CUSTOM_API_KEY=secret\nCUSTOM_BASE_URL=https://upstream.example/v1\n", encoding="utf-8"
    )
    (tmp_path / "state.db").write_text("state", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        hermes_home = Path(kwargs["env"]["HERMES_HOME"])
        captured["sandbox_home"] = hermes_home
        captured["sandbox_config"] = (hermes_home / "config.yaml").read_text(encoding="utf-8")
        captured["sandbox_env"] = (hermes_home / ".env").read_text(encoding="utf-8")
        captured["state_target"] = (hermes_home / "state.db").resolve()
        return _DummyProc()

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/hermes")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
    monkeypatch.delenv("CUSTOM_BASE_URL", raising=False)

    code = await run_client(43123, ["chat"], client="hermes", proxy_mode="reverse")

    assert code == 0
    env = captured["env"]
    assert env["OPENAI_BASE_URL"] == "http://127.0.0.1:43123/v1"
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:43123"
    assert env["OPENROUTER_BASE_URL"] == "http://127.0.0.1:43123/v1"
    assert env["CUSTOM_BASE_URL"] == "http://127.0.0.1:43123/v1"
    assert "base_url: http://127.0.0.1:43123/v1" in captured["sandbox_config"]
    assert "CUSTOM_API_KEY=secret" in captured["sandbox_env"]
    assert "CUSTOM_BASE_URL=http://127.0.0.1:43123/v1" in captured["sandbox_env"]
    assert captured["state_target"] == tmp_path / "state.db"
    assert not captured["sandbox_home"].exists()
    assert (tmp_path / "config.yaml").read_text(encoding="utf-8") == original_config
    # Reverse mode for hermes must not inject the codex-only -c flag
    assert captured["cmd"] == ("/tmp/hermes", "chat")


@pytest.mark.asyncio
async def test_run_client_hermes_capture_only_reverse_sets_multi_provider_urls(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    (tmp_path / "config.yaml").write_text("model:\n  provider: custom\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/hermes")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(43123, ["chat"], client="hermes", proxy_mode="reverse", capture_only=True)

    assert code == 0
    env = captured["env"]
    assert env["OPENAI_BASE_URL"] == "http://127.0.0.1:43123/v1"
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:43123"
    assert env["OPENROUTER_BASE_URL"] == "http://127.0.0.1:43123/v1"
    assert env["CUSTOM_BASE_URL"] == "http://127.0.0.1:43123/v1"
    # Reverse mode for hermes must not inject the codex-only -c flag
    assert captured["cmd"] == ("/tmp/hermes", "chat")
