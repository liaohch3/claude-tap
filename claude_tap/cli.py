"""CLI entry points for claude-tap."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from aiohttp import web

from claude_tap.live import LiveViewerServer
from claude_tap.proxy import proxy_handler
from claude_tap.trace import TraceWriter
from claude_tap.viewer import _generate_html_viewer

# Ensure print output is visible immediately (uv tool pipes stdout with full buffering)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

log = logging.getLogger("claude-tap")

__version__ = "0.1.5"


async def run_claude(port: int, extra_args: list[str]) -> int:
    if shutil.which("claude") is None:
        print(
            "\nError: 'claude' command not found in PATH.\n"
            "Please install Claude Code first: "
            "https://docs.anthropic.com/en/docs/claude-code\n"
        )
        return 1

    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{port}"
    env["NO_PROXY"] = "127.0.0.1"
    # Bypass Claude Code nesting detection
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_SSE_PORT", None)

    cmd = ["claude"] + extra_args
    print(f"\n🚀 Starting Claude Code: {' '.join(cmd)}")
    print(f"   ANTHROPIC_BASE_URL=http://127.0.0.1:{port}\n")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        env=env,
        stdin=None,
        stdout=None,
        stderr=None,
    )

    # Forward SIGINT to child
    loop = asyncio.get_running_loop()

    def _fwd_signal():
        if proc.returncode is None:
            proc.send_signal(signal.SIGINT)

    try:
        loop.add_signal_handler(signal.SIGINT, _fwd_signal)
    except NotImplementedError:
        pass

    code = await proc.wait()

    # Remove signal handler so Ctrl+C works normally during cleanup
    try:
        loop.remove_signal_handler(signal.SIGINT)
    except (NotImplementedError, OSError):
        pass

    print(f"\n📋 Claude Code exited with code {code}")
    return code


async def async_main(args: argparse.Namespace):
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    trace_path = output_dir / f"trace_{ts}.jsonl"
    log_path = output_dir / f"trace_{ts}.log"

    # Start live viewer server if requested
    live_server: LiveViewerServer | None = None
    if args.live_viewer:
        live_server = LiveViewerServer(trace_path, port=args.live_port)
        await live_server.start()
        print(f"🌐 Live viewer: {live_server.url}")
        webbrowser.open(live_server.url)

    writer = TraceWriter(trace_path, live_server=live_server)

    # Proxy logs go to file, not terminal (avoids polluting Claude TUI)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
    log.addHandler(file_handler)
    log.setLevel(logging.DEBUG)
    # Suppress aiohttp access logs
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)

    session = aiohttp.ClientSession(auto_decompress=False)

    app = web.Application()
    app["trace_ctx"] = {
        "target_url": args.target,
        "writer": writer,
        "session": session,
        "turn_counter": 0,
    }
    app.router.add_route("*", "/{path_info:.*}", proxy_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", args.port)
    await site.start()

    # Resolve actual port (site._server is a private API; fall back to args.port)
    try:
        actual_port = site._server.sockets[0].getsockname()[1]
    except (AttributeError, IndexError, OSError):
        actual_port = args.port
    print(f"🔍 claude-tap v{__version__} listening on http://127.0.0.1:{actual_port}")
    print(f"📁 Trace file: {trace_path}")

    # Background update check
    if not args.no_update_check:
        try:
            latest = await _check_pypi_version()
            if latest and _version_tuple(latest) > _version_tuple(__version__):
                print(f"⬆️  Update available: {__version__} → {latest}")
                if not args.no_auto_update:
                    installer = _detect_installer()
                    _start_background_update(installer)
                    print(f"   Downloading update in background ({installer})...")
        except Exception:
            pass

    exit_code = 0
    try:
        if not args.no_launch:
            try:
                exit_code = await run_claude(actual_port, args.claude_args)
            except asyncio.CancelledError:
                pass
        else:
            print("\n--no-launch mode: proxy running. Press Ctrl+C to stop.")
            try:
                while True:
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                pass
    finally:
        try:
            await session.close()
        except Exception:
            pass
        try:
            await runner.cleanup()
        except Exception:
            pass

        # Stop live viewer server if running
        if live_server:
            try:
                await live_server.stop()
            except Exception:
                pass

        # Close writer before generating HTML
        writer.close()

        # Generate self-contained HTML viewer
        html_path = trace_path.with_suffix(".html")
        _generate_html_viewer(trace_path, html_path)

        # Register trace and cleanup old ones
        trace_files = [trace_path.name, log_path.name]
        if html_path.exists():
            trace_files.append(html_path.name)
        _register_trace(output_dir, ts, trace_files)
        if args.max_traces > 0:
            cleaned = _cleanup_traces(output_dir, args.max_traces)
            if cleaned:
                print(f"\n🧹 Cleaned up {cleaned} old trace(s)")

        # Print summary with cost estimation
        stats = writer.get_summary()
        print("\n📊 Trace summary:")
        print(f"   API calls: {stats['api_calls']}")

        # Token breakdown
        total_tokens = stats["input_tokens"] + stats["output_tokens"]
        if total_tokens > 0:
            print(f"   Tokens: {stats['input_tokens']:,} in / {stats['output_tokens']:,} out", end="")
            if stats["cache_read_tokens"] > 0:
                print(f" / {stats['cache_read_tokens']:,} cache_read", end="")
            if stats["cache_create_tokens"] > 0:
                print(f" / {stats['cache_create_tokens']:,} cache_write", end="")
            print()

        # Output files
        print(f"   Trace: {trace_path}")
        print(f"   Log:   {log_path}")
        print(f"   View:  {html_path}")

        # Open viewer in browser if requested
        if args.open_viewer and html_path.exists():
            print("\n🌐 Opening viewer in browser...")
            webbrowser.open(f"file://{html_path.absolute()}")

    return exit_code


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse argv, extracting ``--tap-*`` flags for ourselves and forwarding
    everything else to ``claude``.
    """
    if argv is None:
        argv = sys.argv[1:]

    tap_parser = argparse.ArgumentParser(
        prog="claude-tap",
        description="Trace Claude Code API requests via a local reverse proxy. "
        "All flags not listed below are forwarded to claude.",
    )
    tap_parser.add_argument("-v", "--version", action="version", version=f"%(prog)s {__version__}")
    tap_parser.add_argument(
        "--tap-output-dir", default="./.traces", dest="output_dir", help="Trace output directory (default: ./.traces)"
    )
    tap_parser.add_argument("--tap-port", type=int, default=0, dest="port", help="Proxy port (default: 0 = auto)")
    tap_parser.add_argument(
        "--tap-target",
        default="https://api.anthropic.com",
        dest="target",
        help="Upstream API URL (default: https://api.anthropic.com)",
    )
    tap_parser.add_argument(
        "--tap-no-launch", action="store_true", dest="no_launch", help="Only start the proxy, don't launch Claude"
    )
    tap_parser.add_argument(
        "--tap-open", action="store_true", dest="open_viewer", help="Open HTML viewer in browser after exit"
    )
    tap_parser.add_argument(
        "--tap-live",
        action="store_true",
        dest="live_viewer",
        help="Start real-time viewer server (auto-opens browser)",
    )
    tap_parser.add_argument(
        "--tap-live-port",
        type=int,
        default=0,
        dest="live_port",
        help="Port for live viewer server (default: auto)",
    )
    tap_parser.add_argument(
        "--tap-max-traces",
        type=int,
        default=50,
        dest="max_traces",
        help="Max trace sessions to keep (default: 50, 0 = unlimited)",
    )
    tap_parser.add_argument(
        "--tap-no-update-check",
        action="store_true",
        dest="no_update_check",
        help="Disable PyPI update check on startup",
    )
    tap_parser.add_argument(
        "--tap-no-auto-update",
        action="store_true",
        dest="no_auto_update",
        help="Check for updates but don't auto-download",
    )
    args, claude_args = tap_parser.parse_known_args(argv)
    # Strip leading "--" separator if present (argparse leaves it in remainder)
    if claude_args and claude_args[0] == "--":
        claude_args = claude_args[1:]
    args.claude_args = claude_args
    return args


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
        if installer == "uv":
            cmd = ["uv", "tool", "upgrade", "claude-tap"]
        else:
            cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "claude-tap"]
        return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Trace cleanup – manifest-based
# ---------------------------------------------------------------------------

_MANIFEST_FILE = ".cloudtap-manifest.json"


def _load_manifest(output_dir: Path) -> dict:
    """Load or create the manifest file."""
    manifest_path = output_dir / _MANIFEST_FILE
    if manifest_path.exists():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            if data.get("_cloudtap"):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    manifest = {"_cloudtap": True, "version": __version__, "traces": []}
    _maybe_migrate_existing(output_dir, manifest)
    _save_manifest(output_dir, manifest)
    return manifest


def _save_manifest(output_dir: Path, manifest: dict) -> None:
    """Save manifest to disk."""
    manifest_path = output_dir / _MANIFEST_FILE
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _register_trace(output_dir: Path, ts: str, trace_files: list[str]) -> dict:
    """Register a new trace session in the manifest."""
    manifest = _load_manifest(output_dir)
    entry = {
        "timestamp": ts,
        "files": trace_files,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest["traces"].append(entry)
    _save_manifest(output_dir, manifest)
    return manifest


def _cleanup_traces(output_dir: Path, max_traces: int) -> int:
    """Remove oldest traces exceeding max_traces. Returns count of deleted sessions."""
    if max_traces <= 0:
        return 0
    manifest = _load_manifest(output_dir)
    traces = manifest.get("traces", [])
    if len(traces) <= max_traces:
        return 0
    traces.sort(key=lambda t: t.get("timestamp", ""))
    to_remove = traces[: len(traces) - max_traces]
    removed = 0
    for entry in to_remove:
        for fname in entry.get("files", []):
            fpath = output_dir / fname
            if fpath.exists():
                try:
                    fpath.unlink()
                except OSError:
                    pass
        traces.remove(entry)
        removed += 1
    manifest["traces"] = traces
    _save_manifest(output_dir, manifest)
    return removed


def _maybe_migrate_existing(output_dir: Path, manifest: dict) -> None:
    """Auto-register existing trace_*.jsonl files that are not yet in the manifest."""
    known_files: set[str] = set()
    for entry in manifest.get("traces", []):
        known_files.update(entry.get("files", []))

    for jsonl in sorted(output_dir.glob("trace_*.jsonl")):
        if jsonl.name in known_files:
            continue
        stem = jsonl.stem
        ts = stem.replace("trace_", "", 1)
        files = [jsonl.name]
        for suffix in [".log", ".html"]:
            companion = jsonl.with_suffix(suffix)
            if companion.exists():
                files.append(companion.name)
        manifest["traces"].append({
            "timestamp": ts,
            "files": files,
            "created_at": datetime.fromtimestamp(jsonl.stat().st_mtime, tz=timezone.utc).isoformat(),
        })


def main_entry() -> None:
    """Entry point for the claude-tap CLI."""
    # Check if first argument is "export" subcommand
    if len(sys.argv) > 1 and sys.argv[1] == "export":
        from claude_tap.export import export_main

        sys.exit(export_main(sys.argv[2:]))

    args = parse_args()
    try:
        code = asyncio.run(async_main(args))
    except KeyboardInterrupt:
        code = 0
    sys.exit(code)
