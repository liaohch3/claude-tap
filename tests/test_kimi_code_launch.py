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
    _patch_kimi_code_config_text,
    _prepare_kimi_code_reverse_sandbox,
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
    assert "KIMI_BASE_URL" not in env or env.get("KIMI_BASE_URL") != "http://127.0.0.1:43123"
    config = tomllib.loads((sandbox / "config.toml").read_text(encoding="utf-8"))
    provider = config["providers"]["managed:kimi-code"]
    assert provider["base_url"] == "http://127.0.0.1:43123"


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

    sandbox, _ = _prepare_kimi_code_reverse_sandbox(43123)
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

    sandbox, _ = _prepare_kimi_code_reverse_sandbox(43123)
    try:
        assert (sandbox / _KIMI_CODE_SKIP_MIGRATION_MARKER).read_text(encoding="utf-8") == "keep"
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

    sandbox, providers = _prepare_kimi_code_reverse_sandbox(43123)
    try:
        assert (sandbox / "oauth").is_symlink()
        assert (sandbox / "oauth").resolve() == oauth_dir.resolve()
        assert (sandbox / "credentials").is_symlink()
        assert (sandbox / "credentials").resolve() == credentials_dir.resolve()
        assert providers == ["managed:kimi-code"]
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def test_kimi_code_reverse_trace_options_do_not_strip_path_prefix() -> None:
    options = _reverse_proxy_trace_options("kimi-code", "https://api.kimi.com/coding/v1")
    assert options == {"strip_path_prefix": "", "force_http": False}
