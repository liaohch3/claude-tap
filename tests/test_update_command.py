from __future__ import annotations

import subprocess
import sys

import pytest

from claude_tap.cli import (
    _build_update_command,
    _detect_installer,
    _maybe_start_background_update,
    main_entry,
    parse_update_args,
    update_main,
)


def test_detect_installer_uses_uv_tool_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "claude_tap.cli_update.sys.executable",
        r"C:\Users\alice\AppData\Roaming\uv\data\tools\claude-tap\Scripts\python.exe",
    )
    monkeypatch.setattr("claude_tap.cli_update.sys.platform", "win32")
    monkeypatch.setattr("claude_tap.cli_update.shutil.which", lambda _name: r"C:\tools\uv.exe")

    assert _detect_installer() == "uv"


def test_detect_installer_honors_custom_uv_tool_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "claude_tap.cli_update.sys.executable",
        r"D:\managed-tools\claude-tap\Scripts\python.exe",
    )
    monkeypatch.setenv("UV_TOOL_DIR", r"D:\managed-tools")
    monkeypatch.setattr("claude_tap.cli_update.sys.platform", "win32")
    monkeypatch.setattr("claude_tap.cli_update.shutil.which", lambda _name: None)

    assert _detect_installer() == "uv"


def test_detect_installer_does_not_treat_uv_on_path_as_windows_uv_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("claude_tap.cli_update.sys.executable", r"C:\Python312\python.exe")
    monkeypatch.setattr("claude_tap.cli_update.sys.platform", "win32")
    monkeypatch.setattr("claude_tap.cli_update.shutil.which", lambda _name: r"C:\tools\uv.exe")

    assert _detect_installer() == "pip"


def test_detect_installer_keeps_non_windows_uv_path_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("claude_tap.cli_update.sys.executable", "/usr/bin/python3")
    monkeypatch.setattr("claude_tap.cli_update.sys.platform", "linux")
    monkeypatch.setattr("claude_tap.cli_update.shutil.which", lambda _name: "/usr/bin/uv")

    assert _detect_installer() == "uv"


def test_windows_pip_auto_update_is_replaced_with_manual_instructions(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("claude_tap.cli_update.sys.platform", "win32")
    monkeypatch.setattr("claude_tap.cli_update._detect_installer", lambda: "pip")
    monkeypatch.setattr(
        "claude_tap.cli_update._start_background_update",
        lambda _installer: pytest.fail("Windows pip installs must not update in the background"),
    )

    _maybe_start_background_update(
        no_auto_update=False,
        dashboard_stop_command="claude-tap dashboard stop --tap-live-port 3000 --tap-host 0.0.0.0",
    )

    out = capsys.readouterr().out
    assert "Automatic updates are disabled for pip installs on Windows." in out
    assert "claude-tap dashboard stop --tap-live-port 3000 --tap-host 0.0.0.0" in out
    assert f'"{sys.executable}" -m pip install --upgrade claude-tap' in out
    assert "claude-tap update" not in out


def test_no_auto_update_flag_skips_installer_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "claude_tap.cli_update._detect_installer",
        lambda: pytest.fail("installer detection must not run when auto-update is disabled"),
    )

    _maybe_start_background_update(no_auto_update=True)


def test_safe_auto_update_starts_in_background(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    started: list[str] = []
    monkeypatch.setattr("claude_tap.cli_update.sys.platform", "linux")
    monkeypatch.setattr("claude_tap.cli_update._detect_installer", lambda: "pip")
    monkeypatch.setattr(
        "claude_tap.cli_update._start_background_update",
        lambda installer: started.append(installer) or object(),
    )

    _maybe_start_background_update(no_auto_update=False)

    assert started == ["pip"]
    assert "Downloading update in background (pip)..." in capsys.readouterr().out


def test_failed_background_update_does_not_claim_download_started(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("claude_tap.cli_update.sys.platform", "linux")
    monkeypatch.setattr("claude_tap.cli_update._detect_installer", lambda: "uv")
    monkeypatch.setattr("claude_tap.cli_update._start_background_update", lambda _installer: None)

    _maybe_start_background_update(no_auto_update=False)

    assert "Downloading update in background" not in capsys.readouterr().out


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
