from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

from claude_tap import macos_bundle
from claude_tap.cli import main_entry
from claude_tap.macos_bundle import build_macos_app_bundle


def test_build_macos_app_bundle_writes_double_clickable_app(tmp_path: Path) -> None:
    def fake_compile(_source: str, output_path: Path) -> None:
        output_path.write_bytes(b"native-launcher")

    app_path = build_macos_app_bundle(
        tmp_path / "Claude Tap.app",
        python_executable=sys.executable,
        source_root=Path("/repo/claude-tap"),
        compile_launcher=fake_compile,
    )

    assert app_path == tmp_path / "Claude Tap.app"
    info_path = app_path / "Contents" / "Info.plist"
    launcher_path = app_path / "Contents" / "MacOS" / "claude-tap-macos"
    resources_path = app_path / "Contents" / "Resources"

    assert info_path.exists()
    assert launcher_path.exists()
    assert resources_path.is_dir()
    assert os.access(launcher_path, os.X_OK)

    info = plistlib.loads(info_path.read_bytes())
    assert info["CFBundleName"] == "Claude Tap"
    assert info["CFBundleExecutable"] == "claude-tap-macos"
    assert info["LSUIElement"] is True

    assert launcher_path.read_bytes() == b"native-launcher"


def test_build_macos_app_bundle_uses_native_launcher(tmp_path: Path) -> None:
    compiled: dict[str, str | Path] = {}

    def fake_compile(source: str, output_path: Path) -> None:
        compiled["source"] = source
        compiled["output_path"] = output_path
        output_path.write_bytes(b"native-launcher")

    app_path = build_macos_app_bundle(
        tmp_path / "Claude Tap.app",
        python_executable="/usr/bin/python3",
        source_root=Path("/repo/claude-tap"),
        compile_launcher=fake_compile,
    )

    launcher_path = app_path / "Contents" / "MacOS" / "claude-tap-macos"
    source = compiled["source"]

    assert compiled["output_path"] == launcher_path
    assert isinstance(source, str)
    assert "/usr/bin/python3" in source
    assert "claude_tap" in source
    assert "macos-app" in source
    assert "/repo/claude-tap" in source
    assert launcher_path.read_bytes() == b"native-launcher"


def test_build_macos_app_bundle_can_embed_pyinstaller_executable(tmp_path: Path) -> None:
    compiled: dict[str, str] = {}

    def fake_compile(source: str, output_path: Path) -> None:
        compiled["source"] = source
        output_path.write_bytes(b"native-launcher")

    def fake_build_frozen(resources_dir: Path) -> Path:
        executable = resources_dir / "claude-tap" / "claude-tap"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"frozen")
        return executable

    app_path = build_macos_app_bundle(
        tmp_path / "Claude Tap.app",
        self_contained=True,
        compile_launcher=fake_compile,
        build_frozen_executable=fake_build_frozen,
    )

    source = compiled["source"]
    assert (app_path / "Contents" / "Resources" / "claude-tap" / "claude-tap").read_bytes() == b"frozen"
    assert "_NSGetExecutablePath" in source
    assert "../Resources/claude-tap/claude-tap" in source
    assert 'child_argv[1] = "macos-app";' in source
    assert '"-m"' not in source
    assert '"claude_tap"' not in source


def test_build_macos_app_main_uses_installed_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    built: dict[str, object] = {}

    def fake_build(app_path: Path, **kwargs: object) -> Path:
        built["app_path"] = app_path
        built["kwargs"] = kwargs
        return app_path

    monkeypatch.setattr(macos_bundle, "build_macos_app_bundle", fake_build)

    assert macos_bundle.main(["--output", str(tmp_path / "Tap"), "--installed"]) == 0

    assert built["app_path"] == tmp_path / "Tap"
    assert isinstance(built["kwargs"], dict)
    assert built["kwargs"]["source_root"] is None
    assert "Built macOS app:" in capsys.readouterr().out


def test_build_macos_app_bundle_self_contained_disables_source_root(tmp_path: Path) -> None:
    compiled: dict[str, str] = {}

    def fake_compile(source: str, output_path: Path) -> None:
        compiled["source"] = source
        output_path.write_bytes(b"native-launcher")

    def fake_build_frozen(resources_dir: Path) -> Path:
        executable = resources_dir / "claude-tap" / "claude-tap"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"frozen")
        return executable

    app_path = build_macos_app_bundle(
        tmp_path / "Claude Tap",
        source_root=Path("/repo/should-not-be-used"),
        self_contained=True,
        compile_launcher=fake_compile,
        build_frozen_executable=fake_build_frozen,
    )

    assert app_path == tmp_path / "Claude Tap.app"
    assert "/repo/should-not-be-used" not in compiled["source"]


def test_build_pyinstaller_executable_reports_subprocess_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(macos_bundle.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="PyInstaller: boom"):
        macos_bundle._build_pyinstaller_executable(tmp_path)


def test_build_pyinstaller_executable_requires_expected_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        macos_bundle.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
    )

    with pytest.raises(RuntimeError, match="did not create expected executable"):
        macos_bundle._build_pyinstaller_executable(tmp_path)


def test_compile_native_launcher_reports_missing_compiler(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(macos_bundle, "_native_compiler", lambda: None)

    with pytest.raises(RuntimeError, match="requires clang or cc"):
        macos_bundle._compile_native_launcher("int main(void) { return 0; }", tmp_path / "launcher")


def test_compile_native_launcher_reports_compile_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source_paths: list[Path] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        source_paths.append(Path(cmd[1]))
        return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="compile failed")

    monkeypatch.setattr(macos_bundle, "_native_compiler", lambda: "cc")
    monkeypatch.setattr(macos_bundle.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="compile failed"):
        macos_bundle._compile_native_launcher("bad c", tmp_path / "launcher")

    assert source_paths
    assert not source_paths[0].exists()


def test_ad_hoc_sign_app_uses_codesign_when_available(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    monkeypatch.setattr(macos_bundle.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "codesign" else None)
    monkeypatch.setattr(
        macos_bundle.subprocess,
        "run",
        lambda cmd, **_kwargs: calls.append(cmd),
    )

    macos_bundle._ad_hoc_sign_app(tmp_path / "Claude Tap.app")

    assert calls == [["/usr/bin/codesign", "--force", "--sign", "-", str(tmp_path / "Claude Tap.app")]]


def test_c_string_literal_escapes_quotes_backslashes_and_utf8() -> None:
    literal = macos_bundle._c_string_literal('quote" slash\\ snowman \u2603')

    assert literal.startswith('"')
    assert '\\"' in literal
    assert "\\\\" in literal
    assert "\\xe2\\x98\\x83" in literal


def test_main_entry_routes_build_macos_app_subcommand(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, object] = {}

    def fake_build_main(argv: list[str]) -> int:
        called["argv"] = argv
        return 5

    monkeypatch.setattr(sys, "argv", ["claude-tap", "build-macos-app", "--output", "dist/Test.app"])
    monkeypatch.setattr("claude_tap.macos_bundle.main", fake_build_main)

    with pytest.raises(SystemExit) as excinfo:
        main_entry()

    assert excinfo.value.code == 5
    assert called["argv"] == ["--output", "dist/Test.app"]
