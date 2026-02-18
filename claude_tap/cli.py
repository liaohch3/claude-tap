"""CLI entry points for claude-tap."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shutil
import signal
import sys
import webbrowser
from datetime import datetime
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
    args, claude_args = tap_parser.parse_known_args(argv)
    # Strip leading "--" separator if present (argparse leaves it in remainder)
    if claude_args and claude_args[0] == "--":
        claude_args = claude_args[1:]
    args.claude_args = claude_args
    return args


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
