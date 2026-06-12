from __future__ import annotations

import asyncio
import json
import shutil
import tomllib
from pathlib import Path

import pytest

from claude_tap import parse_args
from claude_tap.cli_clients import (
    _KIMI_CODE_SKIP_MIGRATION_MARKER,
    CLIENT_CONFIGS,
    _detect_kimi_code_target,
    _kimi_code_migration_already_handled,
    _materialize_kimi_code_session_index,
    _merge_kimi_code_session_index,
    _normalize_kimi_code_fs_path,
    _patch_kimi_code_config_text,
    _persist_kimi_code_sandbox,
    _prepare_kimi_code_reverse_sandbox,
    _remap_kimi_code_sandbox_paths,
    _reverse_proxy_trace_options,
    run_client,
)


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


def test_kimi_code_registered_in_client_configs() -> None:
    cfg = CLIENT_CONFIGS["kimi-code"]
    assert cfg.cmd == "kimi"
    assert cfg.label == "Kimi Code CLI"
    assert cfg.install_url == "https://github.com/MoonshotAI/kimi-code"
    assert cfg.base_url_env == "KIMI_CODE_BASE_URL"
    assert cfg.default_target == "https://api.kimi.com/coding/v1"
    assert cfg.default_proxy_mode == "reverse"


def test_kimi_legacy_unchanged_in_client_configs() -> None:
    cfg = CLIENT_CONFIGS["kimi"]
    assert cfg.base_url_env == "KIMI_BASE_URL"
    assert cfg.install_url == "https://github.com/MoonshotAI/kimi-cli"


def test_parse_args_kimi_code_defaults_to_reverse_mode() -> None:
    args = parse_args(["--tap-client", "kimi-code"])
    assert args.client == "kimi-code"
    assert args.target == "https://api.kimi.com/coding/v1"
    assert args.proxy_mode == "reverse"


def test_detect_kimi_code_target_reads_managed_provider(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "kimi-code-home"
    home.mkdir()
    (home / "config.toml").write_text(
        """
default_model = "kimi-code/kimi-for-coding"

[providers."managed:kimi-code"]
type = "kimi"
base_url = "https://api.kimi.com/coding/v1"
api_key = ""

[models."kimi-code/kimi-for-coding"]
provider = "managed:kimi-code"
model = "kimi-for-coding"
max_context_size = 262144
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    assert _detect_kimi_code_target() == "https://api.kimi.com/coding/v1"


def test_detect_kimi_code_target_uses_shell_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KIMI_BASE_URL", "https://gateway.example.com/v1")

    assert _detect_kimi_code_target() == "https://gateway.example.com/v1"


def test_detect_kimi_code_target_uses_kimi_code_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KIMI_CODE_BASE_URL", "https://managed.example.com/coding/v1")

    assert _detect_kimi_code_target() == "https://managed.example.com/coding/v1"


def test_detect_kimi_code_target_model_arg_overrides_stale_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "kimi-code-home"
    home.mkdir()
    (home / "config.toml").write_text(
        """
[models.selected]
provider = "managed:kimi-code"
model = "kimi-for-coding"

[providers."managed:kimi-code"]
type = "kimi"
base_url = "https://selected.example.com/coding/v1"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    monkeypatch.setenv("KIMI_MODEL_NAME", "stale")
    monkeypatch.setenv("KIMI_MODEL_BASE_URL", "https://stale.example.com/v1")

    assert _detect_kimi_code_target(["-m", "selected"]) == "https://selected.example.com/coding/v1"


def test_detect_kimi_code_target_model_arg_without_base_url_uses_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "kimi-code-home"
    home.mkdir()
    (home / "config.toml").write_text(
        """
[models.selected]
provider = "managed:kimi-code"
model = "kimi-for-coding"

[providers."managed:kimi-code"]
type = "kimi"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    monkeypatch.setenv("KIMI_MODEL_BASE_URL", "https://stale.example.com/v1")

    assert _detect_kimi_code_target(["-m", "selected"]) == "https://api.kimi.com/coding/v1"


def test_detect_kimi_code_target_ignores_inactive_model_base_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "kimi-code-home"
    home.mkdir()
    (home / "config.toml").write_text(
        """
default_model = "selected"

[models.selected]
provider = "managed:kimi-code"
model = "kimi-for-coding"

[providers."managed:kimi-code"]
type = "kimi"
base_url = "https://selected.example.com/coding/v1"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    monkeypatch.setenv("KIMI_MODEL_BASE_URL", "https://stale.example.com/v1")

    assert _detect_kimi_code_target() == "https://selected.example.com/coding/v1"


def test_detect_kimi_code_target_reads_config_file_arg(tmp_path: Path) -> None:
    override_config = tmp_path / "override.toml"
    override_config.write_text(
        """
default_model = "custom/model"

[providers."custom"]
type = "kimi"
base_url = "https://custom.example.com/v1"

[models."custom/model"]
provider = "custom"
model = "model"
""".strip(),
        encoding="utf-8",
    )

    assert _detect_kimi_code_target(["--config-file", str(override_config)]) == "https://custom.example.com/v1"


def test_patch_kimi_code_config_text_rewrites_provider_base_url() -> None:
    source = """
[providers."managed:kimi-code"]
type = "kimi"
base_url = "https://api.kimi.com/coding/v1"
api_key = ""
""".strip()
    patched, providers = _patch_kimi_code_config_text(source, "http://127.0.0.1:43123")
    assert 'base_url = "http://127.0.0.1:43123"' in patched
    assert providers == ["managed:kimi-code"]


def test_patch_kimi_code_config_text_rewrites_custom_gateway_provider() -> None:
    source = """
[providers."custom-gateway"]
type = "kimi"
base_url="https://gateway.example.com/v1"
api_key = ""
""".strip()
    patched, providers = _patch_kimi_code_config_text(source, "http://127.0.0.1:43123")
    assert 'base_url="http://127.0.0.1:43123"' in patched
    assert providers == ["custom-gateway"]


def test_patch_kimi_code_config_text_rewrites_env_kimi_base_url() -> None:
    source = """
[providers."env-only"]
type = "kimi"

[providers."env-only".env]
KIMI_BASE_URL = "https://gateway.example.com/v1"
""".strip()
    patched, providers = _patch_kimi_code_config_text(source, "http://127.0.0.1:43123")
    assert 'KIMI_BASE_URL = "http://127.0.0.1:43123"' in patched
    assert providers == ["env-only"]


def test_patch_kimi_code_config_text_rewrites_inline_comments() -> None:
    source = """
[providers."managed:kimi-code"]
type = "kimi"
base_url = "https://api.kimi.com/coding/v1" # production endpoint
api_key = ""
""".strip()
    patched, providers = _patch_kimi_code_config_text(source, "http://127.0.0.1:43123")

    assert 'base_url = "http://127.0.0.1:43123" # production endpoint' in patched
    assert providers == ["managed:kimi-code"]


def test_patch_kimi_code_config_text_inserts_missing_provider_base_url() -> None:
    source = """
[providers."managed:kimi-code"]
type = "kimi"
api_key = ""
""".strip()
    patched, providers = _patch_kimi_code_config_text(source, "http://127.0.0.1:43123")

    assert '[providers."managed:kimi-code"]\nbase_url = "http://127.0.0.1:43123"\n' in patched
    assert providers == ["managed:kimi-code"]


def test_patch_kimi_code_config_text_only_rewrites_selected_provider() -> None:
    source = """
default_model = "custom/model"

[providers."managed:kimi-code"]
type = "kimi"
base_url = "https://api.kimi.com/coding/v1"
api_key = ""

[providers."custom"]
type = "kimi"
base_url = "https://gateway.example.com/v1"
api_key = ""

[models."custom/model"]
provider = "custom"
model = "model"
max_context_size = 1000
""".strip()
    patched, providers = _patch_kimi_code_config_text(source, "http://127.0.0.1:43123")

    assert 'base_url = "https://api.kimi.com/coding/v1"' in patched
    assert 'base_url = "http://127.0.0.1:43123"' in patched
    assert providers == ["custom"]


@pytest.mark.asyncio
async def test_run_client_kimi_code_reverse_sets_kimi_code_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}
    home = tmp_path / "source-home"
    home.mkdir()
    (home / "config.toml").write_text(
        """
[providers."managed:kimi-code"]
type = "kimi"
base_url = "https://api.kimi.com/coding/v1"
api_key = "sk-test"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/kimi")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("claude_tap.cli_clients.shutil.rmtree", lambda *_args, **_kwargs: None)

    code = await run_client(43123, ["--thinking"], client="kimi-code", proxy_mode="reverse")

    assert code == 0
    assert captured["cmd"] == ("/tmp/kimi", "--thinking")
    env = captured["env"]
    sandbox = Path(env["KIMI_CODE_HOME"])
    assert sandbox.is_dir()
    assert env["KIMI_CODE_BASE_URL"] == "http://127.0.0.1:43123"
    assert env["KIMI_BASE_URL"] == "http://127.0.0.1:43123"
    config = tomllib.loads((sandbox / "config.toml").read_text(encoding="utf-8"))
    provider = config["providers"]["managed:kimi-code"]
    assert provider["base_url"] == "http://127.0.0.1:43123"


@pytest.mark.asyncio
async def test_run_client_kimi_code_reverse_rewrites_config_file_arg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}
    home = tmp_path / "source-home"
    home.mkdir()
    override_config = tmp_path / "override.toml"
    override_config.write_text(
        """
[providers."managed:kimi-code"]
type = "kimi"
base_url = "https://gateway.example.com/v1"
api_key = "sk-test"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/kimi")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("claude_tap.cli_clients.shutil.rmtree", lambda *_args, **_kwargs: None)

    code = await run_client(
        43123,
        ["--config-file", str(override_config), "--prompt", "hi"],
        client="kimi-code",
        proxy_mode="reverse",
    )

    assert code == 0
    cmd = captured["cmd"]
    assert cmd[1] == "--config-file"
    patched_config = Path(cmd[2])
    assert "claude_tap_kimi_code_" in str(patched_config)
    assert patched_config.read_text(encoding="utf-8").count("http://127.0.0.1:43123") == 1
    assert str(override_config) not in cmd


@pytest.mark.asyncio
async def test_run_client_kimi_code_reverse_rewrites_model_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}
    home = tmp_path / "source-home"
    home.mkdir()
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    monkeypatch.setenv("KIMI_MODEL_NAME", "env-model")
    monkeypatch.setenv("KIMI_MODEL_BASE_URL", "https://gateway.example.com/v1")

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/kimi")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(43123, ["--prompt", "hi"], client="kimi-code", proxy_mode="reverse")

    assert code == 0
    env = captured["env"]
    assert env["KIMI_MODEL_BASE_URL"] == "http://127.0.0.1:43123"
    assert env["KIMI_BASE_URL"] == "http://127.0.0.1:43123"


@pytest.mark.asyncio
async def test_run_client_kimi_code_reverse_drops_inactive_model_base_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}
    home = tmp_path / "source-home"
    home.mkdir()
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    monkeypatch.setenv("KIMI_MODEL_BASE_URL", "https://stale.example.com/v1")

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/kimi")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(43123, ["--prompt", "hi"], client="kimi-code", proxy_mode="reverse")

    assert code == 0
    env = captured["env"]
    assert "KIMI_MODEL_BASE_URL" not in env
    assert env["KIMI_BASE_URL"] == "http://127.0.0.1:43123"


@pytest.mark.asyncio
async def test_run_client_kimi_code_reverse_does_not_proxy_non_kimi_model_env_without_base_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}
    home = tmp_path / "source-home"
    home.mkdir()
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    monkeypatch.setenv("KIMI_MODEL_NAME", "claude-env")
    monkeypatch.setenv("KIMI_MODEL_PROVIDER_TYPE", "anthropic")

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/kimi")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(43123, ["--prompt", "hi"], client="kimi-code", proxy_mode="reverse")

    assert code == 0
    env = captured["env"]
    assert "KIMI_MODEL_BASE_URL" not in env
    assert env["KIMI_MODEL_NAME"] == "claude-env"
    assert env["KIMI_BASE_URL"] == "http://127.0.0.1:43123"


@pytest.mark.asyncio
async def test_run_client_kimi_code_model_arg_clears_model_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}
    home = tmp_path / "source-home"
    home.mkdir()
    (home / "config.toml").write_text(
        """
[models.selected]
provider = "managed:kimi-code"
model = "kimi-for-coding"

[providers."managed:kimi-code"]
type = "kimi"
base_url = "https://selected.example.com/coding/v1"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    monkeypatch.setenv("KIMI_MODEL_NAME", "stale")
    monkeypatch.setenv("KIMI_MODEL_BASE_URL", "https://stale.example.com/v1")

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/kimi")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(43123, ["-m", "selected", "--prompt", "hi"], client="kimi-code", proxy_mode="reverse")

    assert code == 0
    env = captured["env"]
    assert "KIMI_MODEL_NAME" not in env
    assert "KIMI_MODEL_BASE_URL" not in env
    assert env["KIMI_BASE_URL"] == "http://127.0.0.1:43123"


def test_kimi_code_migration_already_handled_reads_legacy_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    real_home = tmp_path / "real-kimi-code"
    real_home.mkdir()
    legacy = tmp_path / ".kimi"
    legacy.mkdir()
    (legacy / ".migrated-to-kimi-code").write_text(
        json.dumps({"target_path": str(real_home)}),
        encoding="utf-8",
    )
    monkeypatch.setattr("claude_tap.cli_clients.Path.home", lambda: tmp_path)

    assert _kimi_code_migration_already_handled(real_home) is True
    assert _kimi_code_migration_already_handled(tmp_path / "other-home") is False


def test_prepare_kimi_code_reverse_sandbox_writes_skip_marker_when_migrated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    real_home = tmp_path / "real-kimi-code"
    real_home.mkdir()
    legacy = tmp_path / ".kimi"
    legacy.mkdir()
    (legacy / ".migrated-to-kimi-code").write_text(
        json.dumps({"target_path": str(real_home)}),
        encoding="utf-8",
    )
    (real_home / "config.toml").write_text(
        '[providers."managed:kimi-code"]\ntype = "kimi"\nbase_url = "https://api.kimi.com/coding/v1"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("claude_tap.cli_clients.Path.home", lambda: tmp_path)
    monkeypatch.setenv("KIMI_CODE_HOME", str(real_home))

    sandbox, _, _, _ = _prepare_kimi_code_reverse_sandbox(43123)
    try:
        assert (sandbox / _KIMI_CODE_SKIP_MIGRATION_MARKER).is_file()
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def test_prepare_kimi_code_reverse_sandbox_copies_existing_skip_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    real_home = tmp_path / "real-kimi-code"
    real_home.mkdir()
    (real_home / _KIMI_CODE_SKIP_MIGRATION_MARKER).write_text("keep", encoding="utf-8")
    (real_home / "config.toml").write_text(
        '[providers."managed:kimi-code"]\ntype = "kimi"\nbase_url = "https://api.kimi.com/coding/v1"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("KIMI_CODE_HOME", str(real_home))

    sandbox, _, _, _ = _prepare_kimi_code_reverse_sandbox(43123)
    try:
        assert (sandbox / _KIMI_CODE_SKIP_MIGRATION_MARKER).read_text(encoding="utf-8") == "keep"
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def test_prepare_kimi_code_reverse_sandbox_replaces_placeholder_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    real_home = tmp_path / "real-kimi-code"
    real_home.mkdir()
    (real_home / "config.toml").write_text(
        "# Placeholder created by kimi-code before login.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("KIMI_CODE_HOME", str(real_home))

    sandbox, providers, _, _ = _prepare_kimi_code_reverse_sandbox(43123)
    try:
        config = tomllib.loads((sandbox / "config.toml").read_text(encoding="utf-8"))
        assert providers == ["managed:kimi-code"]
        assert config["providers"]["managed:kimi-code"]["base_url"] == "http://127.0.0.1:43123"
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def test_prepare_kimi_code_reverse_sandbox_symlinks_auth_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "source-home"
    oauth_dir = home / "oauth"
    oauth_dir.mkdir(parents=True)
    (oauth_dir / "kimi-code").write_text("", encoding="utf-8")
    credentials_dir = home / "credentials"
    credentials_dir.mkdir(parents=True)
    (credentials_dir / "kimi-code.json").write_text('{"access_token":"test"}', encoding="utf-8")
    (home / "config.toml").write_text(
        '[providers."managed:kimi-code"]\ntype = "kimi"\nbase_url = "https://api.kimi.com/coding/v1"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))

    sandbox, providers, _, _ = _prepare_kimi_code_reverse_sandbox(43123)
    try:
        assert (sandbox / "oauth").is_symlink()
        assert (sandbox / "oauth").resolve() == oauth_dir.resolve()
        assert (sandbox / "credentials").is_symlink()
        assert (sandbox / "credentials").resolve() == credentials_dir.resolve()
        assert providers == ["managed:kimi-code"]
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def test_prepare_kimi_code_reverse_sandbox_creates_auth_dirs_for_first_login(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "source-home"
    home.mkdir()
    (home / "config.toml").write_text(
        '[providers."managed:kimi-code"]\ntype = "kimi"\nbase_url = "https://api.kimi.com/coding/v1"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))

    sandbox, _, _, _ = _prepare_kimi_code_reverse_sandbox(43123)
    try:
        assert (home / "oauth").is_dir()
        assert (home / "credentials").is_dir()
        assert (sandbox / "oauth").is_symlink()
        assert (sandbox / "credentials").is_symlink()
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def test_prepare_kimi_code_reverse_sandbox_preserves_non_kimi_provider_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "source-home"
    home.mkdir()
    (home / "config.toml").write_text(
        """
default_model = "gpt/test"

[models."gpt/test"]
provider = "openai"
model = "gpt-test"

[providers.openai]
type = "openai"
base_url = "https://openai.example.com/v1"
api_key = "sk-test"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))

    sandbox, providers, _, _ = _prepare_kimi_code_reverse_sandbox(43123)
    try:
        config = (sandbox / "config.toml").read_text(encoding="utf-8")
        assert providers == []
        assert 'type = "openai"' in config
        assert 'base_url = "https://openai.example.com/v1"' in config
        assert "managed:kimi-code" not in config
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def test_prepare_kimi_code_reverse_sandbox_links_sessions_and_mcp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "source-home"
    sessions_dir = home / "sessions"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "abc.jsonl").write_text("{}", encoding="utf-8")
    plugins_dir = home / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "installed.json").write_text("[]", encoding="utf-8")
    skills_dir = home / "skills"
    skills_dir.mkdir()
    (skills_dir / "custom-skill.md").write_text("skill", encoding="utf-8")
    (home / "AGENTS.md").write_text("agent instructions", encoding="utf-8")
    (home / "mcp.json").write_text("{}", encoding="utf-8")
    (home / "tui.toml").write_text('theme = "dark"\n', encoding="utf-8")
    (home / "config.toml").write_text(
        '[providers."managed:kimi-code"]\ntype = "kimi"\nbase_url = "https://api.kimi.com/coding/v1"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))

    sandbox, _, _, _ = _prepare_kimi_code_reverse_sandbox(43123)
    try:
        assert (sandbox / "sessions").is_symlink()
        assert (sandbox / "sessions").resolve() == sessions_dir.resolve()
        assert (sandbox / "plugins").is_symlink()
        assert (sandbox / "plugins").resolve() == plugins_dir.resolve()
        assert (sandbox / "skills").is_symlink()
        assert (sandbox / "skills").resolve() == skills_dir.resolve()
        assert (sandbox / "AGENTS.md").is_symlink()
        assert (sandbox / "AGENTS.md").resolve() == (home / "AGENTS.md").resolve()
        assert (sandbox / "mcp.json").is_symlink()
        assert (sandbox / "mcp.json").resolve() == (home / "mcp.json").resolve()
        assert (sandbox / "tui.toml").is_symlink()
        assert (sandbox / "tui.toml").resolve() == (home / "tui.toml").resolve()
        assert (sandbox / "session_index.jsonl").is_file()
        assert not (sandbox / "session_index.jsonl").is_symlink()
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def test_prepare_kimi_code_reverse_sandbox_copies_when_symlinks_fail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "source-home"
    oauth_dir = home / "oauth"
    oauth_dir.mkdir(parents=True)
    (oauth_dir / "kimi-code").write_text("oauth-token", encoding="utf-8")
    credentials_dir = home / "credentials"
    credentials_dir.mkdir()
    (credentials_dir / "kimi-code.json").write_text('{"access_token":"test"}', encoding="utf-8")
    (credentials_dir / "stale.json").write_text('{"access_token":"old"}', encoding="utf-8")
    plugins_dir = home / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "installed.json").write_text('["old-plugin"]', encoding="utf-8")
    skills_dir = home / "skills"
    skills_dir.mkdir()
    (skills_dir / "custom-skill.md").write_text("old skill", encoding="utf-8")
    (home / "AGENTS.md").write_text("old instructions", encoding="utf-8")
    sessions_dir = home / "sessions"
    sessions_dir.mkdir()
    (sessions_dir / "abc.jsonl").write_text("{}", encoding="utf-8")
    (home / "mcp.json").write_text("{}", encoding="utf-8")
    (home / "tui.toml").write_text('theme = "dark"\n', encoding="utf-8")
    (home / "config.toml").write_text(
        '[providers."managed:kimi-code"]\ntype = "kimi"\nbase_url = "https://api.kimi.com/coding/v1"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))

    def fail_symlink(self: Path, target: Path, target_is_directory: bool = False) -> None:
        raise OSError("symlink unavailable")

    monkeypatch.setattr(Path, "symlink_to", fail_symlink)

    sandbox, _, _, _ = _prepare_kimi_code_reverse_sandbox(43123)
    try:
        assert not (sandbox / "oauth").is_symlink()
        assert (sandbox / "oauth" / "kimi-code").read_text(encoding="utf-8") == "oauth-token"
        assert (sandbox / "credentials" / "kimi-code.json").is_file()
        assert (sandbox / "plugins" / "installed.json").is_file()
        assert (sandbox / "skills" / "custom-skill.md").is_file()
        assert (sandbox / "AGENTS.md").is_file()
        assert (sandbox / "sessions" / "abc.jsonl").is_file()
        assert (sandbox / "mcp.json").is_file()
        assert (sandbox / "tui.toml").is_file()

        (sandbox / "oauth" / "new-login").write_text("persisted", encoding="utf-8")
        (sandbox / "credentials" / "stale.json").unlink()
        (sandbox / "plugins" / "installed.json").write_text('["new-plugin"]', encoding="utf-8")
        (sandbox / "skills" / "custom-skill.md").write_text("new skill", encoding="utf-8")
        (sandbox / "AGENTS.md").write_text("new instructions", encoding="utf-8")
        (sandbox / "tui.toml").write_text('theme = "light"\n', encoding="utf-8")
        _persist_kimi_code_sandbox(home, sandbox)

        assert (home / "oauth" / "new-login").read_text(encoding="utf-8") == "persisted"
        assert not (home / "credentials" / "stale.json").exists()
        assert (home / "plugins" / "installed.json").read_text(encoding="utf-8") == '["new-plugin"]'
        assert (home / "skills" / "custom-skill.md").read_text(encoding="utf-8") == "new skill"
        assert (home / "AGENTS.md").read_text(encoding="utf-8") == "new instructions"
        assert (home / "tui.toml").read_text(encoding="utf-8") == 'theme = "light"\n'
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def test_persist_kimi_code_sandbox_writes_config_edits_without_proxy_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "source-home"
    home.mkdir()
    (home / "config.toml").write_text(
        """
[providers."managed:kimi-code"]
type = "kimi"
base_url = "https://api.kimi.com/coding/v1"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))

    sandbox, _, _, _ = _prepare_kimi_code_reverse_sandbox(43123)
    try:
        sandbox_config = sandbox / "config.toml"
        sandbox_config.write_text(
            sandbox_config.read_text(encoding="utf-8") + '\n[ui]\ntheme = "dark"\n',
            encoding="utf-8",
        )

        _persist_kimi_code_sandbox(home, sandbox)

        persisted = (home / "config.toml").read_text(encoding="utf-8")
        assert "http://127.0.0.1:43123" not in persisted
        assert 'base_url = "https://api.kimi.com/coding/v1"' in persisted
        assert 'theme = "dark"' in persisted
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def test_materialize_kimi_code_session_index_rewrites_session_dir(tmp_path: Path) -> None:
    source_home = tmp_path / "home"
    sandbox = tmp_path / "sandbox"
    source_home.mkdir()
    sandbox.mkdir()
    session_dir = source_home / "sessions" / "wd_demo_abcd1234" / "session_test-id"
    session_dir.mkdir(parents=True)
    entry = {
        "sessionId": "session_test-id",
        "sessionDir": str(session_dir),
        "workDir": "/tmp/demo",
    }
    (source_home / "session_index.jsonl").write_text(json.dumps(entry) + "\n", encoding="utf-8")

    _materialize_kimi_code_session_index(source_home, sandbox)

    index = json.loads((sandbox / "session_index.jsonl").read_text(encoding="utf-8").strip())
    expected_dir = _normalize_kimi_code_fs_path(str(sandbox / "sessions" / "wd_demo_abcd1234" / "session_test-id"))
    assert index["sessionDir"] == expected_dir
    assert not index["sessionDir"].startswith("/private/")


def test_materialize_kimi_code_session_index_skips_malformed_rows(tmp_path: Path) -> None:
    source_home = tmp_path / "home"
    sandbox = tmp_path / "sandbox"
    source_home.mkdir()
    sandbox.mkdir()
    session_dir = source_home / "sessions" / "wd_demo_abcd1234" / "session_test-id"
    session_dir.mkdir(parents=True)
    entry = {
        "sessionId": "session_test-id",
        "sessionDir": str(session_dir),
        "workDir": "/tmp/demo",
    }
    (source_home / "session_index.jsonl").write_text(
        "{not json}\n" + json.dumps(entry) + "\n[1, 2, 3]\n",
        encoding="utf-8",
    )

    _materialize_kimi_code_session_index(source_home, sandbox)

    lines = (sandbox / "session_index.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    index = json.loads(lines[0])
    expected_dir = _normalize_kimi_code_fs_path(str(sandbox / "sessions" / "wd_demo_abcd1234" / "session_test-id"))
    assert index["sessionId"] == "session_test-id"
    assert index["sessionDir"] == expected_dir


def test_merge_kimi_code_session_index_skips_malformed_rows(tmp_path: Path) -> None:
    source_home = tmp_path / "home"
    sandbox = tmp_path / "sandbox"
    source_home.mkdir()
    sandbox.mkdir()
    source_session_dir = source_home / "sessions" / "wd_demo_source" / "session_source"
    sandbox_session_dir = sandbox / "sessions" / "wd_demo_new" / "session_new"
    source_session_dir.mkdir(parents=True)
    sandbox_session_dir.mkdir(parents=True)
    source_entry = {
        "sessionId": "session_source",
        "sessionDir": str(source_session_dir),
        "workDir": "/tmp/source",
    }
    sandbox_entry = {
        "sessionId": "session_new",
        "sessionDir": str(sandbox_session_dir),
        "workDir": "/tmp/new",
    }
    (source_home / "session_index.jsonl").write_text(
        "{bad source row}\n" + json.dumps(source_entry) + "\n",
        encoding="utf-8",
    )
    (sandbox / "session_index.jsonl").write_text(
        "{bad sandbox row}\n" + json.dumps(sandbox_entry) + "\n",
        encoding="utf-8",
    )

    _merge_kimi_code_session_index(source_home, sandbox)

    entries = [
        json.loads(line) for line in (source_home / "session_index.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    by_id = {entry["sessionId"]: entry for entry in entries}
    assert set(by_id) == {"session_source", "session_new"}
    assert by_id["session_source"]["sessionDir"] == _normalize_kimi_code_fs_path(str(source_session_dir))
    assert by_id["session_new"]["sessionDir"] == _normalize_kimi_code_fs_path(
        str(source_home / "sessions" / "wd_demo_new" / "session_new")
    )


def test_remap_kimi_code_sandbox_paths_rewrites_session_index_and_state(tmp_path: Path) -> None:
    source_home = tmp_path / "home"
    sandbox = tmp_path / "claude_tap_kimi_code_test"
    source_home.mkdir()
    sandbox.mkdir()
    session_dir = source_home / "sessions" / "wd_demo_abcd1234" / "session_test-id"
    session_dir.mkdir(parents=True)
    sandbox_session_dir = sandbox / "sessions" / "wd_demo_abcd1234" / "session_test-id"
    state = {
        "agents": {
            "main": {
                "homedir": str(sandbox_session_dir / "agents" / "main"),
            }
        }
    }
    (session_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    index_entry = {
        "sessionId": "session_test-id",
        "sessionDir": str(sandbox_session_dir),
        "workDir": "/tmp/demo",
    }
    (source_home / "session_index.jsonl").write_text(json.dumps(index_entry) + "\n", encoding="utf-8")

    _remap_kimi_code_sandbox_paths(source_home, sandbox)

    index = json.loads((source_home / "session_index.jsonl").read_text(encoding="utf-8").strip())
    assert index["sessionDir"] == _normalize_kimi_code_fs_path(str(session_dir))
    updated_state = json.loads((session_dir / "state.json").read_text(encoding="utf-8"))
    assert updated_state["agents"]["main"]["homedir"] == _normalize_kimi_code_fs_path(
        str(session_dir / "agents" / "main")
    )


def test_kimi_code_reverse_trace_options_do_not_strip_path_prefix() -> None:
    options = _reverse_proxy_trace_options("kimi-code", "https://api.kimi.com/coding/v1")
    assert options == {"strip_path_prefix": "", "force_http": False}
