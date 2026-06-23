"""Tests for global_inject: config injection with byte-exact restore."""

from __future__ import annotations

import json
import os
import signal
from pathlib import Path

import pytest

from claude_tap import global_inject
from claude_tap.cli import main_entry


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    return tmp_path


def test_enable_creates_configs_when_absent(_home: Path) -> None:
    global_inject.enable(claude_port=8788, codex_port=8789)

    settings = json.loads((_home / ".claude" / "settings.json").read_text())
    assert settings["env"]["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8788"

    codex = (_home / ".codex" / "config.toml").read_text()
    assert 'openai_base_url = "http://127.0.0.1:8789/v1"' in codex
    assert global_inject.is_active() is True


def test_disable_removes_files_that_did_not_exist(_home: Path) -> None:
    global_inject.enable(claude_port=8788, codex_port=8789)
    global_inject.disable()

    assert not (_home / ".claude" / "settings.json").exists()
    assert not (_home / ".codex" / "config.toml").exists()
    assert global_inject.is_active() is False


def test_disable_restores_existing_files_byte_for_byte(_home: Path) -> None:
    settings_path = _home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    original_settings = '{\n  "env": {\n    "FOO": "bar"\n  },\n  "model": "opus"\n}\n'
    settings_path.write_text(original_settings)

    codex_path = _home / ".codex" / "config.toml"
    codex_path.parent.mkdir(parents=True)
    original_codex = '# my config\nmodel = "gpt-5"\n\n[tui]\ntheme = "dark"\n'
    codex_path.write_text(original_codex)

    global_inject.enable(claude_port=8788, codex_port=8789)
    # While active the base URLs are present.
    assert "127.0.0.1:8788" in settings_path.read_text()
    assert "127.0.0.1:8789" in codex_path.read_text()

    global_inject.disable()
    # After disable the originals return exactly.
    assert settings_path.read_text() == original_settings
    assert codex_path.read_text() == original_codex


def test_enable_preserves_other_claude_settings(_home: Path) -> None:
    settings_path = _home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({"env": {"FOO": "bar"}, "model": "opus"}))

    global_inject.enable(claude_port=8788)
    data = json.loads(settings_path.read_text())
    assert data["env"]["FOO"] == "bar"
    assert data["env"]["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8788"
    assert data["model"] == "opus"


def test_codex_replaces_existing_top_level_key(_home: Path) -> None:
    codex_path = _home / ".codex" / "config.toml"
    codex_path.parent.mkdir(parents=True)
    codex_path.write_text('openai_base_url = "https://old.example/v1"\nmodel = "gpt-5"\n')

    global_inject.enable(codex_port=8789)
    text = codex_path.read_text()
    assert 'openai_base_url = "http://127.0.0.1:8789/v1"' in text
    assert "https://old.example/v1" not in text
    assert text.count("openai_base_url") == 1
    assert 'model = "gpt-5"' in text


def test_codex_inserts_before_first_table(_home: Path) -> None:
    codex_path = _home / ".codex" / "config.toml"
    codex_path.parent.mkdir(parents=True)
    codex_path.write_text('model = "gpt-5"\n\n[tui]\ntheme = "dark"\n')

    global_inject.enable(codex_port=8789)
    lines = codex_path.read_text().splitlines()
    table_idx = lines.index("[tui]")
    url_idx = next(i for i, ln in enumerate(lines) if ln.startswith("openai_base_url"))
    assert url_idx < table_idx


def test_enable_tolerates_invalid_claude_json(_home: Path) -> None:
    settings_path = _home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text("not json{{{")

    global_inject.enable(claude_port=8788)
    data = json.loads(settings_path.read_text())
    assert data["env"]["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8788"


def test_enable_twice_then_disable_restores_original(_home: Path) -> None:
    codex_path = _home / ".codex" / "config.toml"
    codex_path.parent.mkdir(parents=True)
    original = 'model = "gpt-5"\n'
    codex_path.write_text(original)

    global_inject.enable(codex_port=8789)
    global_inject.enable(codex_port=9999)  # second enable must re-baseline backup
    assert "127.0.0.1:9999" in codex_path.read_text()

    global_inject.disable()
    assert codex_path.read_text() == original


def test_enable_overwrites_stale_backup_before_restore(_home: Path) -> None:
    codex_path = _home / ".codex" / "config.toml"
    codex_path.parent.mkdir(parents=True)
    codex_path.write_text('model = "current"\n')
    codex_path.with_name("config.toml.tap-backup").write_text('model = "stale"\n')

    global_inject.enable(codex_port=8789)
    global_inject.disable()

    assert codex_path.read_text() == 'model = "current"\n'


def test_enable_rolls_back_partial_injection_on_failure(_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings_path = _home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    original_settings = '{"env":{"FOO":"bar"}}\n'
    settings_path.write_text(original_settings)

    def fail_codex(*_args: object, **_kwargs: object) -> None:
        raise OSError("codex write failed")

    monkeypatch.setattr(global_inject, "_inject_codex", fail_codex)

    with pytest.raises(OSError, match="codex write failed"):
        global_inject.enable(claude_port=8788, codex_port=8789)

    assert settings_path.read_text() == original_settings
    assert not settings_path.with_name("settings.json.tap-backup").exists()
    assert global_inject.is_active() is False


def test_backup_preserves_existing_config_permissions(_home: Path) -> None:
    settings_path = _home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text('{"env":{"ANTHROPIC_AUTH_TOKEN":"secret"}}\n')
    settings_path.chmod(0o600)

    global_inject.enable(claude_port=8788)

    backup_path = settings_path.with_name("settings.json.tap-backup")
    assert backup_path.exists()
    assert (backup_path.stat().st_mode & 0o777) == 0o600


def test_codex_injects_selected_custom_provider_base_url(_home: Path) -> None:
    codex_path = _home / ".codex" / "config.toml"
    codex_path.parent.mkdir(parents=True)
    codex_path.write_text(
        "\n".join(
            [
                'model_provider = "newapi"',
                "",
                "[model_providers.newapi]",
                'base_url = "https://new-api.example.test/v1"',
                'name = "Custom"',
                "",
            ]
        )
    )

    global_inject.enable(codex_port=8789)

    text = codex_path.read_text()
    assert 'openai_base_url = "http://127.0.0.1:8789/v1"' in text
    assert 'base_url = "http://127.0.0.1:8789/v1"' in text
    assert 'name = "Custom"' in text
    assert "https://new-api.example.test/v1" not in text


def test_claude_injects_custom_bedrock_gateway_when_bedrock_mode_is_enabled(_home: Path) -> None:
    settings_path = _home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps(
            {
                "env": {
                    "CLAUDE_CODE_USE_BEDROCK": "1",
                    "ANTHROPIC_BEDROCK_BASE_URL": "https://ai-gateway.internal.example.com/bedrock",
                }
            }
        )
    )

    global_inject.enable(claude_port=8788)

    env = json.loads(settings_path.read_text())["env"]
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8788"
    assert env["ANTHROPIC_BEDROCK_BASE_URL"] == "http://127.0.0.1:8788"


def test_claude_does_not_rewrite_native_aws_bedrock_url(_home: Path) -> None:
    settings_path = _home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps(
            {
                "env": {
                    "CLAUDE_CODE_USE_BEDROCK": "1",
                    "ANTHROPIC_BEDROCK_BASE_URL": "https://bedrock-runtime.us-east-1.amazonaws.com",
                }
            }
        )
    )

    global_inject.enable(claude_port=8788)

    env = json.loads(settings_path.read_text())["env"]
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8788"
    assert env["ANTHROPIC_BEDROCK_BASE_URL"] == "https://bedrock-runtime.us-east-1.amazonaws.com"


def test_disable_can_terminate_recorded_monitor_processes(_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_file = _home / ".claude-tap" / "monitor-state.json"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(json.dumps({"files": [], "processes": [{"pid": 4321, "role": "claude proxy"}]}))
    signals: list[tuple[int, signal.Signals]] = []

    monkeypatch.setattr(global_inject, "_monitor_process_command", lambda _pid: "python -m claude_tap --tap-no-launch")
    monkeypatch.setattr(global_inject, "_pid_exists", lambda _pid: False)
    monkeypatch.setattr(os, "kill", lambda pid, sig: signals.append((pid, signal.Signals(sig))))

    global_inject.disable(terminate_processes=True)

    assert signals == [(4321, signal.SIGTERM)]
    assert not state_file.exists()


def test_disable_is_noop_without_state(_home: Path) -> None:
    global_inject.disable()  # should not raise
    assert global_inject.is_active() is False


def test_main_entry_routes_monitor_restore(monkeypatch: pytest.MonkeyPatch) -> None:
    restored: list[str] = []

    monkeypatch.setattr("sys.argv", ["claude-tap", "monitor-restore"])
    monkeypatch.setattr(
        "claude_tap.global_inject.disable",
        lambda *, terminate_processes=False: restored.append(str(terminate_processes)),
    )

    with pytest.raises(SystemExit) as excinfo:
        main_entry()

    assert excinfo.value.code == 0
    assert restored == ["True"]
