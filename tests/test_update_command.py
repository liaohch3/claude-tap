from __future__ import annotations

import subprocess
import sys

import pytest

from claude_tap.cli import _build_update_command, main_entry, parse_update_args, update_main


def test_build_update_command_uses_uv_shim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda name: "/tmp/uv" if name == "uv" else None)

    assert _build_update_command("uv") == ["/tmp/uv", "tool", "upgrade", "claude-tap"]


def test_build_update_command_returns_none_when_uv_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _name: None)

    assert _build_update_command("uv") is None


def test_build_update_command_uses_current_python_for_pip() -> None:
    assert _build_update_command("pip") == [sys.executable, "-m", "pip", "install", "--upgrade", "claude-tap"]


def test_parse_update_args_defaults_to_auto() -> None:
    args = parse_update_args([])

    assert args.installer == "auto"
    assert args.dry_run is False


def test_update_main_dry_run_prints_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda name: "/tmp/uv" if name == "uv" else None)

    assert update_main(["--installer", "uv", "--dry-run"]) == 0

    out = capsys.readouterr().out
    assert "Upgrading claude-tap with uv" in out
    assert "/tmp/uv tool upgrade claude-tap" in out


def test_update_main_runs_selected_command(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, 7)

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert update_main(["--installer", "pip"]) == 7
    assert captured["cmd"] == [sys.executable, "-m", "pip", "install", "--upgrade", "claude-tap"]
    assert captured["kwargs"] == {"check": False}


def test_update_main_reports_missing_uv(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _name: None)

    assert update_main(["--installer", "uv"]) == 1

    err = capsys.readouterr().err
    assert "uv" in err
    assert "--installer pip" in err


def test_main_entry_routes_update_subcommand(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, object] = {}

    def fake_update_main(argv):
        called["argv"] = argv
        return 3

    monkeypatch.setattr(sys, "argv", ["claude-tap", "update", "--installer", "pip"])
    monkeypatch.setattr("claude_tap.cli.update_main", fake_update_main)

    with pytest.raises(SystemExit) as excinfo:
        main_entry()

    assert excinfo.value.code == 3
    assert called["argv"] == ["--installer", "pip"]
