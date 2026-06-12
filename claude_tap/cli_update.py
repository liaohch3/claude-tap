"""Update helpers for claude-tap CLI."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request

from claude_tap.process_utils import windows_no_console_subprocess_kwargs

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
    """Detect whether claude-tap was installed via uv or pip.

    Detection strategy:
    1. Check ``sys.executable`` for uv-managed venv paths (most reliable).
       ``uv tool install`` creates a dedicated venv under the uv tools directory
       (e.g. ``.../uv/tools/claude-tap/Scripts/python.exe`` on Windows or
       ``.../uv/tools/claude-tap/bin/python`` on Unix).
    2. On non-Windows only, fall back to checking if *uv* is on ``PATH``.
       Having *uv* on PATH does **not** mean claude-tap was installed by uv;
       on Windows this misclassification would bypass safety guards that
       prevent background pip upgrades while the package is running, so we
       avoid it there.
    3. Default to ``"pip"``.
    """
    exe = (sys.executable or "").lower()
    # uv tool installs create a dedicated venv under the uv tools directory
    if "\\uv\\tools\\" in exe or "/uv/tools/" in exe:
        return "uv"
    # Legacy fallback: if uv is on PATH, assume uv installer.
    # Only used on non-Windows; on Windows, PATH-based detection is too
    # unreliable and misclassifying pip as uv would bypass safety guards.
    if sys.platform != "win32" and shutil.which("uv"):
        return "uv"
    return "pip"


def _is_editable_install() -> bool:
    """Return True if claude-tap is installed in editable/development mode.

    Uses PEP 610 ``direct_url.json`` first (most reliable), then falls back
    to checking whether the package source lives outside known install roots.
    """
    import importlib.metadata
    import site
    import sysconfig

    # --- PEP 610 direct_url.json (most reliable) ---
    try:
        dist = importlib.metadata.distribution("claude-tap")
        raw = dist.read_text("direct_url.json")
        if raw:
            meta = json.loads(raw)
            if meta.get("dir_info", {}).get("editable"):
                return True
    except Exception:
        pass

    # --- Path-based heuristic ---
    try:
        import claude_tap as _pkg

        pkg_file = (_pkg.__file__ or "").replace("\\", "/").lower()
        install_roots: list[str] = []
        for getter in (
            lambda: sysconfig.get_paths().get("purelib", ""),
            lambda: sysconfig.get_paths().get("platlib", ""),
        ):
            root = getter().replace("\\", "/").lower()
            if root:
                install_roots.append(root)
        for sp in site.getsitepackages() + [site.getusersitepackages()]:
            root = sp.replace("\\", "/").lower()
            if root and root not in install_roots:
                install_roots.append(root)
        # If the package file is under a known install root, it is NOT editable
        for root in install_roots:
            if pkg_file.startswith(root):
                return False
        # Outside all known install roots → likely editable
        return True
    except Exception:
        return False


def _start_background_update(installer: str) -> subprocess.Popen | None:
    """Start a background process to upgrade claude-tap."""
    try:
        cmd = _build_update_command(installer)
        if cmd is None:
            return None
        return subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **windows_no_console_subprocess_kwargs(),
        )
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


def _windows_deferred_pip_update(cmd: list[str]) -> int:
    """On Windows, defer pip upgrade until after this process exits.

    pip cannot safely upgrade a package whose modules are loaded in the
    current process on Windows — the running process holds file locks that
    can cause pip to leave behind ``~laude_tap`` debris directories and
    corrupt the installation.  This function writes a small helper script
    that polls for the current process to exit, then runs ``pip install
    --upgrade`` with no locks held.
    """
    pid = os.getpid()
    # Self-deleting helper script: wait for parent PID to exit, then run pip.
    script = (
        "import subprocess, sys, time, os\n"
        f"pid = {pid}\n"
        "while True:\n"
        "    try:\n"
        "        os.kill(pid, 0)\n"
        "    except (OSError, ProcessLookupError):\n"
        "        break\n"
        "    time.sleep(0.5)\n"
        f"result = subprocess.run({cmd!r}, check=False)\n"
        "try:\n"
        "    os.unlink(__file__)\n"
        "except OSError:\n"
        "    pass\n"
        "sys.exit(result.returncode)\n"
    )

    fd, script_path = tempfile.mkstemp(suffix="_claude_tap_update.py")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(script)
    except Exception:
        os.close(fd)
        try:
            os.unlink(script_path)
        except OSError:
            pass
        return 1

    try:
        subprocess.Popen(
            [sys.executable, script_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **windows_no_console_subprocess_kwargs(),
        )
    except OSError:
        try:
            os.unlink(script_path)
        except OSError:
            pass
        return 1

    print("⏳  Windows pip upgrade deferred — will run after this process exits.")
    return 0


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

    # On Windows with pip, the running process holds file locks that make
    # an in-place pip upgrade unsafe.  Defer the upgrade to a helper that
    # waits for this process to exit first.
    if sys.platform == "win32" and installer == "pip":
        return _windows_deferred_pip_update(cmd)

    try:
        result = subprocess.run(cmd, check=False)
    except OSError as exc:
        print(f"Error: failed to run update command: {exc}", file=sys.stderr)
        return 1
    return result.returncode
