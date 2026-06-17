"""Build a local double-clickable macOS app bundle for claude-tap."""

from __future__ import annotations

import argparse
import plistlib
import sys
from pathlib import Path

DEFAULT_APP_NAME = "Claude Tap"
DEFAULT_BUNDLE_ID = "dev.claude-tap.macos"
DEFAULT_EXECUTABLE_NAME = "claude-tap-macos"


def build_macos_app_bundle(
    app_path: Path,
    *,
    python_executable: str | None = None,
    source_root: Path | None = None,
) -> Path:
    """Create a local .app bundle that launches the claude-tap menu bar app."""
    app_path = app_path.expanduser()
    if app_path.suffix != ".app":
        app_path = app_path.with_suffix(".app")

    contents_dir = app_path / "Contents"
    macos_dir = contents_dir / "MacOS"
    resources_dir = contents_dir / "Resources"
    macos_dir.mkdir(parents=True, exist_ok=True)
    resources_dir.mkdir(parents=True, exist_ok=True)

    executable_name = DEFAULT_EXECUTABLE_NAME
    _write_info_plist(contents_dir / "Info.plist", executable_name=executable_name)
    _write_launcher(
        macos_dir / executable_name,
        python_executable=python_executable or sys.executable,
        source_root=source_root,
    )
    return app_path


def parse_build_macos_app_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="claude-tap build-macos-app",
        description="Build a local double-clickable macOS app bundle for claude-tap.",
    )
    parser.add_argument(
        "--output",
        default=str(Path("dist") / "Claude Tap.app"),
        help="Output .app path (default: dist/Claude Tap.app)",
    )
    parser.add_argument(
        "--installed",
        action="store_true",
        help="Do not add the current source checkout to PYTHONPATH in the launcher.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_build_macos_app_args(argv)
    source_root = None if args.installed else Path(__file__).resolve().parents[1]
    app_path = build_macos_app_bundle(
        Path(args.output),
        python_executable=sys.executable,
        source_root=source_root,
    )
    print(f"Built macOS app: {app_path}")
    return 0


def _write_info_plist(path: Path, *, executable_name: str) -> None:
    info = {
        "CFBundleDevelopmentRegion": "en",
        "CFBundleExecutable": executable_name,
        "CFBundleIdentifier": DEFAULT_BUNDLE_ID,
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": DEFAULT_APP_NAME,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "1.0",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "11.0",
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
    }
    path.write_bytes(plistlib.dumps(info, sort_keys=True))


def _write_launcher(path: Path, *, python_executable: str, source_root: Path | None) -> None:
    lines = [
        "#!/bin/sh",
        'export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"',
    ]
    if source_root is not None:
        lines.append(f'export PYTHONPATH="{source_root}${{PYTHONPATH:+:$PYTHONPATH}}"')
    lines.append(f'exec "{python_executable}" -m claude_tap macos-app "$@"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | 0o755)
