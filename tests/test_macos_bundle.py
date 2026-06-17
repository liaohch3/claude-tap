from __future__ import annotations

import os
import plistlib
import sys
from pathlib import Path

import pytest

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
