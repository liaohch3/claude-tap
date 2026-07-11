"""Update helpers for claude-tap CLI."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

from claude_tap.process_utils import windows_no_console_subprocess_kwargs


def _detect_installer() -> str:
    """Detect whether claude-tap was installed via uv or pip."""
    exe = (sys.executable or "").lower().replace("\\", "/")
    uv_tool_dir = os.environ.get("UV_TOOL_DIR", "").lower().replace("\\", "/").rstrip("/")
    if uv_tool_dir and exe.startswith(f"{uv_tool_dir}/"):
        return "uv"
    if "/uv/data/tools/" in exe or "/uv/tools/" in exe:
        return "uv"
    if sys.platform != "win32" and shutil.which("uv"):
        return "uv"
    return "pip"


def _build_update_command(installer: str) -> list[str] | None:
    """Build the manual self-upgrade command."""
    if installer == "uv":
        uv_path = shutil.which("uv")
        if uv_path is None:
            return None
        return [uv_path, "tool", "upgrade", "claude-tap"]
    if installer == "pip":
        return [sys.executable, "-m", "pip", "install", "--upgrade", "claude-tap"]
    raise ValueError(f"unsupported installer: {installer}")


def parse_update_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse arguments for the update subcommand."""
    parser = argparse.ArgumentParser(
        prog="claude-tap update",
        description="Upgrade claude-tap using the detected installer.",
    )
    parser.add_argument(
        "--installer",
        choices=["auto", "uv", "pip"],
        default="auto",
        help="Upgrade backend to use (default: auto-detect uv or pip)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the upgrade command without running it",
    )
    return parser.parse_args(argv)


def update_main(argv: list[str] | None = None) -> int:
    """Entry point for the update subcommand."""
    args = parse_update_args(argv)
    installer = _detect_installer() if args.installer == "auto" else args.installer
    cmd = _build_update_command(installer)
    if cmd is None:
        print("Error: 'uv' command not found. Re-run with --installer pip or install uv.", file=sys.stderr)
        return 1

    printable_cmd = " ".join(cmd)
    print(f"Upgrading claude-tap with {installer}: {printable_cmd}")
    if args.dry_run:
        return 0

    try:
        result = subprocess.run(cmd, check=False, **windows_no_console_subprocess_kwargs())
    except OSError as exc:
        print(f"Error: failed to run update command: {exc}", file=sys.stderr)
        return 1
    return result.returncode
