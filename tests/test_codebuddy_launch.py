from __future__ import annotations

import asyncio
import json

import pytest

from claude_tap import parse_args
from claude_tap.cli import (
    CLIENT_CONFIGS,
    _detect_codebuddy_target,
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


def test_codebuddy_registered_in_client_configs() -> None:
    cfg = CLIENT_CONFIGS["codebuddy"]
    assert cfg.cmd == "codebuddy"
    assert cfg.label == "CodeBuddy"
    # CodeBuddy's OpenAI client appends ``/v2`` to its product endpoint, so
    # the reverse-proxy upstream must include that prefix.
    assert cfg.default_target == "https://copilot.tencent.com/v2"
    assert cfg.base_url_env == "CODEBUDDY_BASE_URL"
    assert cfg.base_url_suffix == ""
    assert cfg.default_proxy_mode == "reverse"
    assert cfg.inject_settings_env is True


def test_parse_args_codebuddy_defaults_to_reverse_mode() -> None:
    args = parse_args(["--tap-client", "codebuddy"])
    assert args.client == "codebuddy"
    assert args.target == "https://copilot.tencent.com/v2"
    assert args.proxy_mode == "reverse"


def test_parse_args_codebuddy_explicit_forward_overrides_default() -> None:
    args = parse_args(["--tap-client", "codebuddy", "--tap-proxy-mode", "forward"])
    assert args.client == "codebuddy"
    assert args.proxy_mode == "forward"


@pytest.mark.asyncio
async def test_run_client_codebuddy_reverse_sets_base_url_and_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/codebuddy")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(43123, ["-p", "Reply OK"], client="codebuddy", proxy_mode="reverse")

    assert code == 0
    env = captured["env"]
    assert env["CODEBUDDY_BASE_URL"] == "http://127.0.0.1:43123"

    # --settings should be injected for CodeBuddy (inject_settings_env=True)
    cmd = captured["cmd"]
    assert cmd[0] == "/tmp/codebuddy"
    assert cmd[1] == "--settings"
    settings = json.loads(cmd[2])
    assert settings["env"]["CODEBUDDY_BASE_URL"] == "http://127.0.0.1:43123"
    assert cmd[3:] == ("-p", "Reply OK")


@pytest.mark.asyncio
async def test_run_client_codebuddy_reverse_does_not_inject_settings_when_already_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/codebuddy")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(
        43123,
        ["--settings", '{"env":{}}', "-p", "Reply OK"],
        client="codebuddy",
        proxy_mode="reverse",
    )

    assert code == 0
    cmd = captured["cmd"]
    # --settings already present in user args; should not be duplicated
    settings_count = sum(1 for arg in cmd if arg == "--settings")
    assert settings_count == 1


@pytest.mark.asyncio
async def test_run_client_codebuddy_forward_sets_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.delenv("CODEBUDDY_BASE_URL", raising=False)
    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/codebuddy")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(43123, ["-p", "hello"], client="codebuddy", proxy_mode="forward")

    assert code == 0
    env = captured["env"]
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:43123"
    assert env["http_proxy"] == "http://127.0.0.1:43123"
    # In forward mode, CODEBUDDY_BASE_URL should NOT be set
    assert "CODEBUDDY_BASE_URL" not in env


def test_codebuddy_reverse_trace_options_do_not_strip_path_prefix() -> None:
    options = _reverse_proxy_trace_options("codebuddy", "https://copilot.tencent.com/v2")

    assert options == {
        "strip_path_prefix": "",
        "force_http": False,
    }


def test_detect_codebuddy_target_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEBUDDY_BASE_URL", "https://copilot.tencent.com/v2")
    assert _detect_codebuddy_target() == "https://copilot.tencent.com/v2"


def test_detect_codebuddy_target_falls_back_to_default(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.delenv("CODEBUDDY_BASE_URL", raising=False)
    # Ensure no settings files and no endpoint cache are found.
    monkeypatch.setattr("claude_tap.cli.Path.cwd", lambda: tmp_path)
    monkeypatch.setattr("claude_tap.cli.Path.home", lambda: tmp_path)
    assert _detect_codebuddy_target() == "https://copilot.tencent.com/v2"


def test_detect_codebuddy_target_reads_login_endpoint_cache(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """CodeBuddy writes its resolved endpoint to ``local_storage/entry_<md5>.info``
    after login. We honor it so internal/iOA/external users all work without
    setting any env var."""
    monkeypatch.delenv("CODEBUDDY_BASE_URL", raising=False)
    monkeypatch.setattr("claude_tap.cli.Path.cwd", lambda: tmp_path)
    monkeypatch.setattr("claude_tap.cli.Path.home", lambda: tmp_path)

    cache_dir = tmp_path / ".codebuddy" / "local_storage"
    cache_dir.mkdir(parents=True)
    # md5("CodeBuddy-Endpoint-Cache") == 933d5543e80177622c17a73869c0fad7
    (cache_dir / "entry_933d5543e80177622c17a73869c0fad7.info").write_text('"https://www.codebuddy.ai"')

    assert _detect_codebuddy_target() == "https://www.codebuddy.ai/v2"


def test_detect_codebuddy_target_reads_settings_from_config_dir(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """When users relocate CodeBuddy's config via ``CODEBUDDY_CONFIG_DIR``,
    the settings lookup must follow the override instead of only reading
    ``~/.codebuddy/settings.json``."""
    monkeypatch.delenv("CODEBUDDY_BASE_URL", raising=False)
    monkeypatch.setattr("claude_tap.cli.Path.cwd", lambda: tmp_path / "cwd")
    monkeypatch.setattr("claude_tap.cli.Path.home", lambda: tmp_path / "home")

    config_dir = tmp_path / "custom-codebuddy"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text('{"env": {"CODEBUDDY_BASE_URL": "https://gateway.example.com/v2"}}')
    monkeypatch.setenv("CODEBUDDY_CONFIG_DIR", str(config_dir))

    assert _detect_codebuddy_target() == "https://gateway.example.com/v2"
