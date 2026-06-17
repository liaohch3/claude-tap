"""Build a local double-clickable macOS app bundle for claude-tap."""

from __future__ import annotations

import argparse
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

DEFAULT_APP_NAME = "Claude Tap"
DEFAULT_BUNDLE_ID = "dev.claude-tap.macos"
DEFAULT_EXECUTABLE_NAME = "claude-tap-macos"


def build_macos_app_bundle(
    app_path: Path,
    *,
    python_executable: str | None = None,
    source_root: Path | None = None,
    compile_launcher: Callable[[str, Path], None] | None = None,
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
    launcher_path = macos_dir / executable_name
    source = _launcher_source(
        python_executable=python_executable or sys.executable,
        source_root=source_root,
    )
    (compile_launcher or _compile_native_launcher)(source, launcher_path)
    launcher_path.chmod(launcher_path.stat().st_mode | 0o755)
    _ad_hoc_sign_app(app_path)
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


def _launcher_source(*, python_executable: str, source_root: Path | None) -> str:
    source_root_literal = _c_string_literal(str(source_root)) if source_root is not None else "NULL"
    return f"""#include <errno.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;

int main(int argc, char **argv) {{
    const char *python = {_c_string_literal(python_executable)};
    const char *source_root = {source_root_literal};
    const char *existing_path = getenv("PATH");
    const char *prefix_path = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin";
    const char *existing_pythonpath = getenv("PYTHONPATH");

    if (existing_path && existing_path[0]) {{
        size_t path_len = strlen(prefix_path) + strlen(existing_path) + 2;
        char *path_value = malloc(path_len);
        if (!path_value) return 126;
        snprintf(path_value, path_len, "%s:%s", prefix_path, existing_path);
        setenv("PATH", path_value, 1);
        free(path_value);
    }} else {{
        setenv("PATH", prefix_path, 1);
    }}

    if (source_root && source_root[0]) {{
        size_t pythonpath_len = strlen(source_root) + 1;
        if (existing_pythonpath && existing_pythonpath[0]) {{
            pythonpath_len += strlen(existing_pythonpath) + 1;
        }}
        char *pythonpath_value = malloc(pythonpath_len);
        if (!pythonpath_value) return 126;
        if (existing_pythonpath && existing_pythonpath[0]) {{
            snprintf(pythonpath_value, pythonpath_len, "%s:%s", source_root, existing_pythonpath);
        }} else {{
            snprintf(pythonpath_value, pythonpath_len, "%s", source_root);
        }}
        setenv("PYTHONPATH", pythonpath_value, 1);
        free(pythonpath_value);
    }}

    char **child_argv = calloc((size_t)argc + 4, sizeof(char *));
    if (!child_argv) return 126;
    child_argv[0] = (char *)python;
    child_argv[1] = "-m";
    child_argv[2] = "claude_tap";
    child_argv[3] = "macos-app";
    for (int i = 1; i < argc; i++) {{
        child_argv[i + 3] = argv[i];
    }}
    child_argv[argc + 3] = NULL;

    // Spawn Python as a child and wait, instead of execv replacing this process.
    // LaunchServices ties the app's menu-bar/GUI identity to this bundle
    // executable; replacing it via execv makes the status item fail to appear.
    pid_t pid;
    int spawn_result = posix_spawn(&pid, python, NULL, NULL, child_argv, environ);
    free(child_argv);
    if (spawn_result != 0) {{
        errno = spawn_result;
        perror("claude-tap macOS launcher");
        return 127;
    }}

    int status = 0;
    while (waitpid(pid, &status, 0) < 0) {{
        if (errno != EINTR) {{
            perror("claude-tap macOS launcher");
            return 127;
        }}
    }}
    if (WIFEXITED(status)) {{
        return WEXITSTATUS(status);
    }}
    return 128;
}}
"""


def _compile_native_launcher(source: str, output_path: Path) -> None:
    compiler = _native_compiler()
    if compiler is None:
        raise RuntimeError("Building Claude Tap.app requires clang or cc. Install Xcode Command Line Tools first.")

    source_path = output_path.with_suffix(".c")
    source_path.write_text(source, encoding="utf-8")
    try:
        result = subprocess.run(
            [compiler, str(source_path), "-o", str(output_path)],
            check=False,
            capture_output=True,
            text=True,
        )
    finally:
        source_path.unlink(missing_ok=True)
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Failed to compile Claude Tap.app launcher: {details}")


def _native_compiler() -> str | None:
    return shutil.which("cc") or shutil.which("clang")


def _ad_hoc_sign_app(app_path: Path) -> None:
    codesign = shutil.which("codesign")
    if not codesign:
        return
    subprocess.run(
        [codesign, "--force", "--sign", "-", str(app_path)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _c_string_literal(value: str) -> str:
    chunks: list[str] = ['"']
    for byte in value.encode("utf-8"):
        if byte == 34:
            chunks.append('\\"')
        elif byte == 92:
            chunks.append("\\\\")
        elif 32 <= byte <= 126:
            chunks.append(chr(byte))
        else:
            chunks.append(f"\\x{byte:02x}")
    chunks.append('"')
    return "".join(chunks)
