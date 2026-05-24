from __future__ import annotations

import subprocess
import sys

import pytest

from claude_tap.cli import (
    _build_update_command,
    _is_editable_install,
    async_main,
    main_entry,
    parse_args,
    parse_update_args,
    update_main,
)


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


def test_is_editable_install_detects_source_tree_path(monkeypatch: pytest.MonkeyPatch) -> None:
    import claude_tap

    monkeypatch.setattr(claude_tap, "__file__", r"D:\work\claude-tap\claude_tap\__init__.py")

    assert _is_editable_install() is True


def test_is_editable_install_returns_false_for_site_packages_install(monkeypatch: pytest.MonkeyPatch) -> None:
    import claude_tap

    monkeypatch.setattr(claude_tap, "__file__", r"C:\Python\Lib\site-packages\claude_tap\__init__.py")

    def fake_distribution(_name):
        class _D:
            def read_text(self, _path):
                return None

        return _D()

    monkeypatch.setattr("importlib.metadata.distribution", fake_distribution)

    assert _is_editable_install() is False


def test_is_editable_install_uses_pep610_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    import claude_tap

    monkeypatch.setattr(claude_tap, "__file__", r"C:\Python\Lib\site-packages\claude_tap\__init__.py")

    def fake_distribution(_name):
        class _D:
            def read_text(self, path):
                if path == "direct_url.json":
                    return '{"url": "file:///src/claude-tap", "dir_info": {"editable": true}}'
                return None

        return _D()

    monkeypatch.setattr("importlib.metadata.distribution", fake_distribution)

    assert _is_editable_install() is True


def test_is_editable_install_ignores_malformed_pep610_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    import claude_tap

    monkeypatch.setattr(claude_tap, "__file__", r"C:\Python\Lib\site-packages\claude_tap\__init__.py")

    def fake_distribution(_name):
        class _D:
            def read_text(self, _path):
                return "{not json"

        return _D()

    monkeypatch.setattr("importlib.metadata.distribution", fake_distribution)

    assert _is_editable_install() is False


@pytest.mark.asyncio
async def test_async_main_skips_background_update_for_editable_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake_check_pypi_version():
        return "99.0.0"

    async def fake_run_client(*_args, **_kwargs):
        return 0

    def fail_background_update(_installer: str):
        raise AssertionError("editable installs must not start a background update")

    monkeypatch.setattr("claude_tap.cli.__version__", "1.0.0")
    monkeypatch.setattr("claude_tap.cli._check_pypi_version", fake_check_pypi_version)
    monkeypatch.setattr("claude_tap.cli._is_editable_install", lambda: True)
    monkeypatch.setattr("claude_tap.cli._start_background_update", fail_background_update)
    monkeypatch.setattr("claude_tap.cli.run_client", fake_run_client)

    args = parse_args(
        [
            "--tap-output-dir",
            str(tmp_path),
            "--tap-no-live",
            "--tap-no-open",
        ]
    )

    assert await async_main(args) == 0
    out = capsys.readouterr().out
    assert "Update available: 1.0.0" in out
    assert "Editable install detected" in out
    assert "Downloading update in background" not in out


@pytest.mark.asyncio
async def test_async_main_keeps_background_update_for_non_editable_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    started: list[str] = []

    async def fake_check_pypi_version():
        return "99.0.0"

    async def fake_run_client(*_args, **_kwargs):
        return 0

    monkeypatch.setattr("claude_tap.cli.__version__", "1.0.0")
    monkeypatch.setattr("claude_tap.cli._check_pypi_version", fake_check_pypi_version)
    monkeypatch.setattr("claude_tap.cli._is_editable_install", lambda: False)
    monkeypatch.setattr("claude_tap.cli._detect_installer", lambda: "pip")
    monkeypatch.setattr("claude_tap.cli._start_background_update", started.append)
    monkeypatch.setattr("claude_tap.cli.run_client", fake_run_client)

    args = parse_args(
        [
            "--tap-output-dir",
            str(tmp_path),
            "--tap-no-live",
            "--tap-no-open",
        ]
    )

    assert await async_main(args) == 0
    assert started == ["pip"]
    out = capsys.readouterr().out
    assert "Update available: 1.0.0" in out
    assert "Downloading update in background (pip)" in out


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
