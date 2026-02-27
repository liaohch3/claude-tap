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
import threading
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from aiohttp import web

from claude_tap.certs import CertificateAuthority, ensure_ca
from claude_tap.forward_proxy import ForwardProxyServer
from claude_tap.live import LiveViewerServer
from claude_tap.proxy import proxy_handler
from claude_tap.trace import TraceWriter
from claude_tap.viewer import _generate_html_viewer

# Ensure print output is visible immediately (uv tool pipes stdout with full buffering)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

log = logging.getLogger("claude-tap")

try:
    from importlib.metadata import version as _pkg_version

    __version__ = _pkg_version("claude-tap")
except Exception:
    __version__ = "0.0.0"


def _open_browser(url: str) -> None:
    """Open URL in browser without blocking. Silently ignores failures in headless environments."""
    threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()


async def run_claude(
    port: int,
    extra_args: list[str],
    client: str = "claude",
    proxy_mode: str = "reverse",
    ca_cert_path: Path | None = None,
) -> int:
    if client == "claude":
        client_cmd = "claude"
        client_label = "Claude Code"
        missing_help = (
            "\nError: 'claude' command not found in PATH.\n"
            "Please install Claude Code first: "
            "https://docs.anthropic.com/en/docs/claude-code\n"
        )
    else:
        client_cmd = "codex"
        client_label = "Codex CLI"
        missing_help = (
            "\nError: 'codex' command not found in PATH.\n"
            "Please install Codex CLI first: "
            "https://github.com/openai/codex\n"
        )

    if shutil.which(client_cmd) is None:
        print(missing_help)
        return 1

    env = os.environ.copy()

    cmd_args = list(extra_args)

    if proxy_mode == "forward":
        proxy_url = f"http://127.0.0.1:{port}"
        # Set both upper/lower-case variants for tools that read one form only.
        env["HTTP_PROXY"] = proxy_url
        env["HTTPS_PROXY"] = proxy_url
        env["ALL_PROXY"] = proxy_url
        env["http_proxy"] = proxy_url
        env["https_proxy"] = proxy_url
        env["all_proxy"] = proxy_url
        if ca_cert_path:
            env["NODE_EXTRA_CA_CERTS"] = str(ca_cert_path)

        if client == "claude":
            # Claude Code may source proxy env from settings rather than process env.
            # Inject equivalent settings unless user already provided --settings.
            has_settings_arg = any(arg == "--settings" or arg.startswith("--settings=") for arg in cmd_args)
            if not has_settings_arg:
                settings_payload: dict[str, dict[str, str]] = {
                    "env": {
                        "HTTP_PROXY": proxy_url,
                        "HTTPS_PROXY": proxy_url,
                        "ALL_PROXY": proxy_url,
                        "http_proxy": proxy_url,
                        "https_proxy": proxy_url,
                        "all_proxy": proxy_url,
                    }
                }
                if ca_cert_path:
                    settings_payload["env"]["NODE_EXTRA_CA_CERTS"] = str(ca_cert_path)
                cmd_args = ["--settings", json.dumps(settings_payload, separators=(",", ":"))] + cmd_args
        # Don't set provider-specific base URL in forward mode
    else:
        if client == "claude":
            env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{port}"
        else:
            env["OPENAI_BASE_URL"] = f"http://127.0.0.1:{port}/v1"
        env["NO_PROXY"] = "127.0.0.1"

    if client == "claude":
        # Bypass Claude Code nesting detection
        env.pop("CLAUDECODE", None)
        env.pop("CLAUDE_CODE_SSE_PORT", None)

    cmd = [client_cmd] + cmd_args
    print(f"\n🚀 Starting {client_label}: {' '.join(cmd)}")
    if proxy_mode == "forward":
        print(f"   HTTPS_PROXY=http://127.0.0.1:{port}")
        if ca_cert_path:
            print(f"   NODE_EXTRA_CA_CERTS={ca_cert_path}")
    else:
        if client == "claude":
            print(f"   ANTHROPIC_BASE_URL=http://127.0.0.1:{port}")
        else:
            print(f"   OPENAI_BASE_URL=http://127.0.0.1:{port}/v1")
    print()

    # Give child its own process group and make it the foreground group
    # so the TUI app has full terminal control (e.g. Cmd+Delete, Ctrl+U).
    use_fg = hasattr(os, "tcsetpgrp") and sys.stdin.isatty()

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        env=env,
        stdin=None,
        stdout=None,
        stderr=None,
        **({"process_group": 0} if use_fg else {}),
    )

    if use_fg:
        try:
            os.tcsetpgrp(sys.stdin.fileno(), proc.pid)
        except OSError:
            pass

    # --- Signal handling: graceful Ctrl+C / Ctrl+Z ---
    loop = asyncio.get_running_loop()

    # Prevent Ctrl+Z from suspending the session
    old_sigtstp = signal.signal(signal.SIGTSTP, signal.SIG_IGN)

    sigint_count = 0

    def _handle_sigint():
        nonlocal sigint_count
        sigint_count += 1
        if sigint_count == 1:
            if proc.returncode is None:
                proc.terminate()
                print(f"\n⏳ Shutting down {client_label}... (Ctrl+C again to force)")
        else:
            if proc.returncode is None:
                proc.kill()

    def _handle_sigtstp():
        if proc.returncode is None:
            proc.terminate()
            print(f"\n⏳ Shutting down {client_label}...")

    try:
        loop.add_signal_handler(signal.SIGINT, _handle_sigint)
        loop.add_signal_handler(signal.SIGTSTP, _handle_sigtstp)
    except (NotImplementedError, OSError):
        pass

    code = await proc.wait()

    # Restore parent as foreground process group.
    # Ignore SIGTTOU first — the parent is still in the background group
    # and any terminal write (including tcsetpgrp) would suspend it.
    if use_fg:
        old_sigttou = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
        try:
            os.tcsetpgrp(sys.stdin.fileno(), os.getpgrp())
        except OSError:
            pass
        signal.signal(signal.SIGTTOU, old_sigttou)

    # Restore original SIGTSTP handler and remove async signal handlers
    signal.signal(signal.SIGTSTP, old_sigtstp)
    try:
        loop.remove_signal_handler(signal.SIGINT)
    except (NotImplementedError, OSError):
        pass
    try:
        loop.remove_signal_handler(signal.SIGTSTP)
    except (NotImplementedError, OSError):
        pass

    print(f"\n📋 {client_label} exited with code {code}")
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
        live_server = LiveViewerServer(trace_path, port=args.live_port, host=args.host)
        await live_server.start()
        print(f"🌐 Live viewer: {live_server.url}")
        _open_browser(live_server.url)

    writer = TraceWriter(trace_path, live_server=live_server)

    # Proxy logs go to file, not terminal (avoids polluting Claude TUI)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
    log.addHandler(file_handler)
    log.setLevel(logging.DEBUG)
    # Suppress aiohttp logs from polluting the terminal
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    # Redirect aiohttp.server errors (e.g. broken connections) to log file only
    aiohttp_server_log = logging.getLogger("aiohttp.server")
    aiohttp_server_log.addHandler(file_handler)
    aiohttp_server_log.propagate = False

    # Honor system proxy env (HTTP_PROXY/HTTPS_PROXY/ALL_PROXY/NO_PROXY) for
    # outbound upstream requests. This is important when users route traffic
    # through tools like Clash/VPN.
    session = aiohttp.ClientSession(auto_decompress=False, trust_env=True)

    # Forward proxy mode: raw TCP server with CONNECT/TLS termination
    # Reverse proxy mode: aiohttp web app (current behavior)
    forward_server: ForwardProxyServer | None = None
    runner: web.AppRunner | None = None
    ca_cert_path: Path | None = None

    if args.proxy_mode == "forward":
        ca_cert_path, ca_key_path = ensure_ca()
        ca = CertificateAuthority(ca_cert_path, ca_key_path)
        forward_server = ForwardProxyServer(
            host=args.host,
            port=args.port,
            ca=ca,
            writer=writer,
            session=session,
        )
        actual_port = await forward_server.start()
        print(f"🔍 claude-tap v{__version__} forward proxy on http://{args.host}:{actual_port}")
        print(f"   CA cert: {ca_cert_path}")
    else:
        app = web.Application(client_max_size=0)  # No body size limit (proxy must forward everything)
        app["trace_ctx"] = {
            "target_url": args.target,
            "writer": writer,
            "session": session,
            "turn_counter": 0,
        }
        app.router.add_route("*", "/{path_info:.*}", proxy_handler)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, args.host, args.port)
        await site.start()

        # Resolve actual port (site._server is a private API; fall back to args.port)
        try:
            actual_port = site._server.sockets[0].getsockname()[1]
        except (AttributeError, IndexError, OSError):
            actual_port = args.port
        print(f"🔍 claude-tap v{__version__} listening on http://{args.host}:{actual_port}")

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
                exit_code = await run_claude(
                    actual_port,
                    args.claude_args,
                    client=args.client,
                    proxy_mode=args.proxy_mode,
                    ca_cert_path=ca_cert_path,
                )
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
        if forward_server:
            try:
                await forward_server.stop()
            except Exception:
                pass
        if runner:
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

        # Open viewer in browser (default: auto-open unless --tap-no-open)
        if args.open_viewer and html_path.exists():
            print("\n🌐 Opening viewer in browser...")
            _open_browser(f"file://{html_path.absolute()}")

    return exit_code


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse argv, extracting ``--tap-*`` flags for ourselves and forwarding
    everything else to the selected client.
    """
    if argv is None:
        argv = sys.argv[1:]

    tap_parser = argparse.ArgumentParser(
        prog="claude-tap",
        description="Trace Claude Code or Codex API requests via a local proxy. "
        "All flags not listed below are forwarded to the selected client.",
    )
    tap_parser.add_argument("-v", "--version", action="version", version=f"%(prog)s {__version__}")
    tap_parser.add_argument(
        "--tap-output-dir", default="./.traces", dest="output_dir", help="Trace output directory (default: ./.traces)"
    )
    tap_parser.add_argument("--tap-port", type=int, default=0, dest="port", help="Proxy port (default: 0 = auto)")
    tap_parser.add_argument(
        "--tap-host",
        default=None,
        dest="host",
        help="Bind address for proxy and live viewer (default: 0.0.0.0 in --tap-no-launch mode, 127.0.0.1 otherwise)",
    )
    tap_parser.add_argument(
        "--tap-client",
        choices=["claude", "codex"],
        default="claude",
        dest="client",
        help="Client to launch (default: claude)",
    )
    tap_parser.add_argument(
        "--tap-target",
        default=None,
        dest="target",
        help="Upstream API URL (default: provider-specific)",
    )
    tap_parser.add_argument(
        "--tap-proxy-mode",
        choices=["reverse", "forward"],
        default="reverse",
        dest="proxy_mode",
        help="Proxy mode: 'reverse' sets provider base URL (default), "
        "'forward' sets HTTPS_PROXY with CONNECT/TLS termination",
    )
    tap_parser.add_argument(
        "--tap-no-launch", action="store_true", dest="no_launch", help="Only start the proxy, don't launch client"
    )
    tap_parser.add_argument(
        "--tap-open",
        action="store_true",
        dest="open_viewer",
        default=True,
        help="Open HTML viewer in browser after exit (default: on)",
    )
    tap_parser.add_argument(
        "--tap-no-open",
        action="store_false",
        dest="open_viewer",
        help="Don't auto-open HTML viewer after exit",
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
    # Default host: 0.0.0.0 in --tap-no-launch mode (proxy-only, typically remote),
    # 127.0.0.1 otherwise (launching the client locally).
    if args.host is None:
        args.host = "0.0.0.0" if args.no_launch else "127.0.0.1"
    if args.target is None:
        args.target = "https://api.anthropic.com" if args.client == "claude" else "https://api.openai.com"
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
        manifest["traces"].append(
            {
                "timestamp": ts,
                "files": files,
                "created_at": datetime.fromtimestamp(jsonl.stat().st_mtime, tz=timezone.utc).isoformat(),
            }
        )


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
