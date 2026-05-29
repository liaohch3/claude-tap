"""Update helpers for claude-tap CLI."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import urllib.request

# ---------------------------------------------------------------------------
# Smart update check
# ---------------------------------------------------------------------------


def _version_tuple(v: str) -> tuple[int, ...]:
    """Parse '0.1.4' into (0, 1, 4) for comparison."""
    return tuple(int(x) for x in v.strip().split(".") if x.isdigit())


async def _check_pypi_version(timeout: float = 3.0) -> str | None:
    """Check PyPI for the latest version. Returns version string or None."""
    url = os.environ.get("CLAUDE_TAP_PYPI_URL", "https://pypi.org/pypi/claude-tap/json")

    def _fetch() -> str | None:
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read())
                return data.get("info", {}).get("version")
        except Exception:
            return None

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch)


def _detect_installer() -> str:
    """Detect whether claude-tap was installed via uv or pip."""
    exe = sys.executable or ""
    if "uv" in exe.lower() or shutil.which("uv"):
        return "uv"
    return "pip"


def _start_background_update(installer: str) -> subprocess.Popen | None:
    """Start a background process to upgrade claude-tap."""
    try:
        cmd = _build_update_command(installer)
        if cmd is None:
            return None
        return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return None


def _build_update_command(installer: str) -> list[str] | None:
    """Build the foreground/background self-upgrade command."""
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
        result = subprocess.run(cmd, check=False)
    except OSError as exc:
        print(f"Error: failed to run update command: {exc}", file=sys.stderr)
        return 1
    return result.returncode
