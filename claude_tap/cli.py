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
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from aiohttp import web

from claude_tap.certs import CertificateAuthority, ensure_ca
from claude_tap.cursor_transcript import import_cursor_transcripts
from claude_tap.forward_proxy import ForwardProxyServer
from claude_tap.live import LiveViewerServer
from claude_tap.proxy import proxy_handler
from claude_tap.trace import TraceWriter
from claude_tap.viewer import _generate_html_viewer

# Force UTF-8 + line-buffered stdout/stderr so emoji output works on Windows
# consoles (GBK/cp936) and `uv tool` doesn't fully buffer our progress prints.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace", line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")

log = logging.getLogger("claude-tap")

try:
    from importlib.metadata import version as _pkg_version

    __version__ = _pkg_version("claude-tap")
except Exception:
    __version__ = "0.0.0"


def _open_browser(url: str) -> None:
    """Open URL in browser without blocking. Silently ignores failures in headless environments."""
    threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()


@dataclass(frozen=True)
class ClientConfig:
    """Per-client configuration for supported AI CLI tools."""

    cmd: str
    label: str
    install_url: str
    base_url_env: str
    base_url_suffix: str  # appended to http://127.0.0.1:{port}
    default_target: str
    nesting_env_keys: tuple[str, ...] = ()  # env vars to clear before launch
    # Default proxy mode when --tap-proxy-mode is not explicitly set.
    # Multi-provider clients (e.g. opencode) default to "forward" so that all
    # provider traffic is captured regardless of which env var the client honors.
    default_proxy_mode: str = "reverse"

    @property
    def missing_help(self) -> str:
        return (
            f"\nError: '{self.cmd}' command not found in PATH.\nPlease install {self.label} first: {self.install_url}\n"
        )

    def reverse_base_url(self, port: int) -> str:
        return f"http://127.0.0.1:{port}{self.base_url_suffix}"


CLIENT_CONFIGS: dict[str, ClientConfig] = {
    "claude": ClientConfig(
        cmd="claude",
        label="Claude Code",
        install_url="https://docs.anthropic.com/en/docs/claude-code",
        base_url_env="ANTHROPIC_BASE_URL",
        base_url_suffix="",
        default_target="https://api.anthropic.com",
        nesting_env_keys=("CLAUDECODE", "CLAUDE_CODE_SSE_PORT"),
    ),
    "codex": ClientConfig(
        cmd="codex",
        label="Codex CLI",
        install_url="https://github.com/openai/codex",
        base_url_env="OPENAI_BASE_URL",
        base_url_suffix="/v1",
        default_target="https://api.openai.com",
    ),
    "opencode": ClientConfig(
        cmd="opencode",
        label="OpenCode",
        install_url="https://opencode.ai/docs/",
        # opencode is multi-provider; ANTHROPIC_BASE_URL is what reverse mode
        # patches when the user explicitly opts out of forward mode. Forward
        # proxy is the default and captures every provider transparently.
        base_url_env="ANTHROPIC_BASE_URL",
        base_url_suffix="",
        default_target="https://api.anthropic.com",
        default_proxy_mode="forward",
    ),
    "cursor": ClientConfig(
        cmd="cursor-agent",
        label="Cursor CLI",
        install_url="https://cursor.com/cli",
        # Cursor CLI does not expose a provider base URL. Keep reverse-mode
        # fields structurally valid, but default to forward proxy mode.
        base_url_env="CURSOR_BASE_URL",
        base_url_suffix="",
        default_target="https://api2.cursor.sh",
        default_proxy_mode="forward",
    ),
}


async def run_client(
    port: int,
    extra_args: list[str],
    client: str = "claude",
    proxy_mode: str = "reverse",
    ca_cert_path: Path | None = None,
) -> int:
    cfg = CLIENT_CONFIGS[client]

    # asyncio.create_subprocess_exec uses CreateProcess on Windows, which only
    # auto-appends `.exe`; resolve here so npm `.cmd`/`.bat` shims also work.
    resolved_cmd = shutil.which(cfg.cmd)
    if resolved_cmd is None:
        print(cfg.missing_help)
        return 1

    env = os.environ.copy()

    cmd_args = list(extra_args)
    has_openai_base_override = _has_config_override(cmd_args, "openai_base_url")

    if proxy_mode == "forward":
        proxy_url = f"http://127.0.0.1:{port}"
        # Set both upper/lower-case variants for tools that read one form only.
        env["HTTP_PROXY"] = proxy_url
        env["HTTPS_PROXY"] = proxy_url
        env["ALL_PROXY"] = proxy_url
        env["http_proxy"] = proxy_url
        env["https_proxy"] = proxy_url
        env["all_proxy"] = proxy_url
        _extend_no_proxy(env, ("localhost", "127.0.0.1", "::1"))
        if ca_cert_path:
            env["NODE_EXTRA_CA_CERTS"] = str(ca_cert_path)
            # Codex is a Rust binary; NODE_EXTRA_CA_CERTS does not affect its TLS stack.
            env["SSL_CERT_FILE"] = str(ca_cert_path)
            env["CODEX_CA_CERTIFICATE"] = str(ca_cert_path)

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
        base_url = cfg.reverse_base_url(port)
        env[cfg.base_url_env] = base_url
        env["NO_PROXY"] = "127.0.0.1"
        if client == "claude":
            has_settings_arg = any(arg == "--settings" or arg.startswith("--settings=") for arg in cmd_args)
            if not has_settings_arg:
                settings_payload = {"env": {cfg.base_url_env: base_url}}
                cmd_args = ["--settings", json.dumps(settings_payload, separators=(",", ":"))] + cmd_args
        if client == "codex" and not has_openai_base_override:
            # Newer Codex builds may ignore OPENAI_BASE_URL in OAuth/WebSocket mode
            # unless the same value is also supplied as a config override.
            cmd_args = ["-c", f'openai_base_url="{base_url}"'] + cmd_args

    for key in cfg.nesting_env_keys:
        env.pop(key, None)

    cmd = [resolved_cmd] + cmd_args
    print(f"\n🚀 Starting {cfg.label}: {' '.join([cfg.cmd, *cmd_args])}")
    if proxy_mode == "forward":
        print(f"   HTTPS_PROXY=http://127.0.0.1:{port}")
        if ca_cert_path:
            print(f"   NODE_EXTRA_CA_CERTS={ca_cert_path}")
    else:
        print(f"   {cfg.base_url_env}={cfg.reverse_base_url(port)}")
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

    # SIGTSTP is Unix-only; on Windows the attribute is absent.
    sigtstp = getattr(signal, "SIGTSTP", None)
    old_sigtstp = signal.signal(sigtstp, signal.SIG_IGN) if sigtstp is not None else None

    sigint_count = 0

    def _handle_sigint():
        nonlocal sigint_count
        sigint_count += 1
        if sigint_count == 1:
            if proc.returncode is None:
                proc.terminate()
                print(f"\n⏳ Shutting down {cfg.label}... (Ctrl+C again to force)")
        else:
            if proc.returncode is None:
                proc.kill()

    def _handle_sigtstp():
        if proc.returncode is None:
            proc.terminate()
            print(f"\n⏳ Shutting down {cfg.label}...")

    try:
        loop.add_signal_handler(signal.SIGINT, _handle_sigint)
        if sigtstp is not None:
            loop.add_signal_handler(sigtstp, _handle_sigtstp)
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
    if sigtstp is not None and old_sigtstp is not None:
        signal.signal(sigtstp, old_sigtstp)
    try:
        loop.remove_signal_handler(signal.SIGINT)
    except (NotImplementedError, OSError):
        pass
    if sigtstp is not None:
        try:
            loop.remove_signal_handler(sigtstp)
        except (NotImplementedError, OSError):
            pass

    print(f"\n📋 {cfg.label} exited with code {code}")
    return code


def _extend_no_proxy(env: dict[str, str], values: tuple[str, ...]) -> None:
    """Append local proxy bypasses without discarding existing settings."""
    existing: list[str] = []
    for key in ("NO_PROXY", "no_proxy"):
        raw = env.get(key, "")
        existing.extend(part.strip() for part in raw.split(",") if part.strip())

    merged: list[str] = []
    seen: set[str] = set()
    for value in [*existing, *values]:
        lowered = value.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        merged.append(value)

    no_proxy = ",".join(merged)
    env["NO_PROXY"] = no_proxy
    env["no_proxy"] = no_proxy


def _has_config_override(args: list[str], key: str) -> bool:
    """Return True when argv already contains a matching -c/--config override."""
    prefixes = (f"{key}=",)
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("-c", "--config"):
            if i + 1 < len(args) and args[i + 1].startswith(prefixes):
                return True
            i += 2
            continue
        if arg.startswith("--config="):
            value = arg.split("=", 1)[1]
            if value.startswith(prefixes):
                return True
        i += 1
    return False


async def async_main(args: argparse.Namespace):
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H%M%S")
    ts = now.strftime("%Y%m%d_%H%M%S")  # kept for manifest compatibility
    date_dir = output_dir / date_str
    date_dir.mkdir(parents=True, exist_ok=True)
    trace_path = date_dir / f"trace_{time_str}.jsonl"
    log_path = date_dir / f"trace_{time_str}.log"

    # Start live viewer server if requested
    live_server: LiveViewerServer | None = None
    if args.live_viewer:
        live_server = LiveViewerServer(trace_path, port=args.live_port, host=args.host, output_dir=output_dir)
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
    # uvloop emits TLS shutdown warnings through the asyncio logger.
    # Keep them in the trace log rather than printing them into the client TUI.
    asyncio_log = logging.getLogger("asyncio")
    asyncio_log.addHandler(file_handler)
    asyncio_log.propagate = False

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
            **_reverse_proxy_trace_options(args.client, args.target),
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
    client_started_at = time.time()
    try:
        if not args.no_launch:
            client_started_at = time.time()
            try:
                exit_code = await run_client(
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
        if forward_server:
            try:
                await asyncio.wait_for(forward_server.stop(), timeout=10)
            except asyncio.TimeoutError:
                log.warning("Timed out stopping forward proxy")
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
        try:
            await asyncio.wait_for(session.close(), timeout=5)
        except asyncio.TimeoutError:
            log.warning("Timed out closing upstream HTTP session")
        except Exception:
            pass

        if args.client == "cursor" and not args.no_launch:
            imported = await import_cursor_transcripts(writer, since=client_started_at)
            if imported:
                print(f"   Cursor transcript turns: {imported}")

        # Close writer before generating HTML
        writer.close()

        # Generate self-contained HTML viewer
        html_path = trace_path.with_suffix(".html")
        _generate_html_viewer(trace_path, html_path)

        # Register trace and cleanup old ones
        trace_files = [_rel_posix(trace_path, output_dir), _rel_posix(log_path, output_dir)]
        if html_path.exists():
            trace_files.append(_rel_posix(html_path, output_dir))
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
            _open_browser(html_path.absolute().as_uri())

    return exit_code


_CODEX_CHATGPT_TARGET = "https://chatgpt.com/backend-api/codex"


def _reverse_proxy_trace_options(client: str, target: str) -> dict[str, object]:
    return {
        "strip_path_prefix": "/v1" if client == "codex" and "api.openai.com" not in target else "",
        "force_http": False,
    }


def _detect_codex_target() -> str:
    """Auto-detect the correct upstream target for Codex CLI.

    Reads ``~/.codex/auth.json`` (or ``$CODEX_HOME/auth.json``) to determine
    the auth mode.  ChatGPT OAuth users (``codex login``) need the chatgpt.com
    backend; API-key users use api.openai.com.
    """
    codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    auth_file = codex_home / "auth.json"
    try:
        data = json.loads(auth_file.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("auth_mode") == "chatgpt":
            return _CODEX_CHATGPT_TARGET
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return CLIENT_CONFIGS["codex"].default_target


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse argv, extracting ``--tap-*`` flags for ourselves and forwarding
    everything else to the selected client.
    """
    if argv is None:
        argv = sys.argv[1:]

    tap_parser = argparse.ArgumentParser(
        prog="claude-tap",
        description="Trace Claude Code, Codex CLI, OpenCode, or Cursor CLI API requests via a local proxy. "
        "All flags not listed below are forwarded to the selected client.",
        epilog=(
            "claude code:\n"
            "  claude-tap                            Basic tracing\n"
            "  claude-tap --tap-live                 Real-time viewer in browser\n"
            "  claude-tap -- --model claude-opus-4-6  Pass flags to Claude Code\n"
            "  claude-tap -- -c                      Continue last conversation\n"
            "  claude-tap -- --dangerously-skip-permissions  Auto-accept tool calls\n"
            "  claude-tap --tap-live -- --dangerously-skip-permissions --model claude-sonnet-4-6\n"
            "\n"
            "codex cli:\n"
            "  # Target is auto-detected from Codex auth state when possible\n"
            "  claude-tap --tap-client codex\n"
            "  # If auto-detection cannot read Codex auth, specify OAuth target explicitly\n"
            "  claude-tap --tap-client codex --tap-target https://chatgpt.com/backend-api/codex\n"
            "  # With model and full auto-approval\n"
            "  claude-tap --tap-client codex -- --model codex-mini-latest --full-auto\n"
            "\n"
            "opencode (multi-provider; defaults to forward proxy mode):\n"
            "  # Forward proxy captures every provider opencode talks to\n"
            "  claude-tap --tap-client opencode\n"
            "  # Force reverse mode (single ANTHROPIC_BASE_URL provider only)\n"
            "  claude-tap --tap-client opencode --tap-proxy-mode reverse\n"
            "\n"
            "cursor cli (defaults to forward proxy mode):\n"
            '  claude-tap --tap-client cursor -- -p --trust --model auto "hello"\n'
            "  # Cursor readable messages are imported from local transcripts after exit\n"
            "\n"
            "proxy-only mode (connect from another terminal):\n"
            "  claude-tap --tap-no-launch --tap-port 8080\n"
            "  # then: ANTHROPIC_BASE_URL=http://127.0.0.1:8080 claude\n"
            "\n"
            "export traces:\n"
            "  claude-tap export trace.jsonl              Export to markdown\n"
            "  claude-tap export trace.jsonl -o out.md    Export to file\n"
            "  claude-tap export trace.jsonl --format json Export as JSON\n"
            "  claude-tap export trace.jsonl -o out.html  Export as HTML viewer\n"
            "\n"
            "dashboard:\n"
            "  claude-tap dashboard                       Browse trace history\n"
            "  claude-tap dashboard --tap-live-port 3000  Use a fixed dashboard port\n"
            "\n"
            "homepage: https://github.com/liaohch3/claude-tap"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    tap_parser.add_argument("-v", "--version", action="version", version=f"%(prog)s {__version__}")

    # -- Proxy options --
    proxy_group = tap_parser.add_argument_group("proxy options")
    proxy_group.add_argument("--tap-port", type=int, default=0, dest="port", help="Proxy port (default: auto)")
    proxy_group.add_argument(
        "--tap-host",
        default=None,
        dest="host",
        help="Bind address (default: 127.0.0.1, or 0.0.0.0 with --tap-no-launch)",
    )
    proxy_group.add_argument(
        "--tap-client",
        choices=["claude", "codex", "opencode", "cursor"],
        default="claude",
        dest="client",
        help="Client to launch (default: claude)",
    )
    proxy_group.add_argument(
        "--tap-target",
        default=None,
        dest="target",
        help="Upstream API URL (default: auto-detected from auth state)",
    )
    proxy_group.add_argument(
        "--tap-proxy-mode",
        choices=["reverse", "forward"],
        default=None,
        dest="proxy_mode",
        help=(
            "'reverse' sets provider base URL, 'forward' sets HTTPS_PROXY with CONNECT/TLS termination. "
            "Default depends on the client: 'reverse' for claude/codex, 'forward' for opencode/cursor."
        ),
    )
    proxy_group.add_argument(
        "--tap-no-launch", action="store_true", dest="no_launch", help="Only start the proxy, don't launch client"
    )

    # -- Viewer options --
    viewer_group = tap_parser.add_argument_group("viewer options")
    viewer_group.add_argument(
        "--tap-no-open",
        action="store_false",
        dest="open_viewer",
        default=True,
        help="Don't auto-open HTML viewer after exit",
    )
    viewer_group.add_argument(
        "--tap-live",
        action="store_true",
        dest="live_viewer",
        help="Start real-time viewer server (auto-opens browser)",
    )
    viewer_group.add_argument(
        "--tap-live-port",
        type=int,
        default=0,
        dest="live_port",
        help="Port for live viewer server (default: auto)",
    )

    # -- Storage & update options --
    storage_group = tap_parser.add_argument_group("storage and update options")
    storage_group.add_argument(
        "--tap-output-dir", default="./.traces", dest="output_dir", help="Trace output directory (default: ./.traces)"
    )
    storage_group.add_argument(
        "--tap-max-traces",
        type=int,
        default=50,
        dest="max_traces",
        help="Max trace sessions to keep (default: 50, 0 = unlimited)",
    )
    storage_group.add_argument(
        "--tap-no-update-check",
        action="store_true",
        dest="no_update_check",
        help="Disable PyPI update check on startup",
    )
    storage_group.add_argument(
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
        if args.client == "codex":
            args.target = _detect_codex_target()
        else:
            args.target = CLIENT_CONFIGS[args.client].default_target
    if args.proxy_mode is None:
        args.proxy_mode = CLIENT_CONFIGS[args.client].default_proxy_mode
    return args


def parse_dashboard_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse arguments for the standalone dashboard command."""
    parser = argparse.ArgumentParser(
        prog="claude-tap dashboard",
        description="Open a local claude-tap dashboard for browsing trace history.",
    )
    parser.add_argument(
        "--tap-output-dir",
        default="./.traces",
        dest="output_dir",
        help="Trace output directory to browse (default: ./.traces)",
    )
    parser.add_argument(
        "--tap-live-port",
        type=int,
        default=0,
        dest="live_port",
        help="Dashboard server port (default: auto)",
    )
    parser.add_argument(
        "--tap-host",
        default="127.0.0.1",
        dest="host",
        help="Bind address (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--tap-no-open",
        action="store_false",
        dest="open_viewer",
        default=True,
        help="Don't auto-open the dashboard in a browser",
    )
    return parser.parse_args(argv)


async def dashboard_main(args: argparse.Namespace) -> int:
    """Run the standalone dashboard until interrupted."""
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    date_dir = output_dir / now.strftime("%Y-%m-%d")
    date_dir.mkdir(parents=True, exist_ok=True)
    trace_path = date_dir / f"dashboard_{now.strftime('%H%M%S')}.jsonl"

    server = LiveViewerServer(trace_path, port=args.live_port, host=args.host, output_dir=output_dir)
    await server.start()
    print(f"🌐 claude-tap dashboard: {server.url}")
    print(f"📁 Trace directory: {output_dir}")
    print("Press Ctrl+C to stop.")
    if args.open_viewer:
        _open_browser(server.url)

    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        await server.stop()
    return 0


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
            uv_path = shutil.which("uv")
            if uv_path is None:
                return None
            cmd = [uv_path, "tool", "upgrade", "claude-tap"]
        else:
            cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "claude-tap"]
        return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Trace cleanup – manifest-based
# ---------------------------------------------------------------------------

_MANIFEST_FILE = ".cloudtap-manifest.json"


def _rel_posix(path: Path, base: Path) -> str:
    # Forward slashes so manifests stay portable when `.traces` is synced across OSes.
    return path.relative_to(base).as_posix()


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
        parents_to_check: set[Path] = set()
        for fname in entry.get("files", []):
            fpath = output_dir / fname
            if fpath.exists():
                parents_to_check.add(fpath.parent)
                try:
                    fpath.unlink()
                except OSError:
                    pass
        # Remove empty date subdirectories
        for parent in parents_to_check:
            if parent != output_dir and parent.is_dir() and not any(parent.iterdir()):
                try:
                    parent.rmdir()
                except OSError:
                    pass
        traces.remove(entry)
        removed += 1
    manifest["traces"] = traces
    _save_manifest(output_dir, manifest)
    return removed


def _maybe_migrate_existing(output_dir: Path, manifest: dict) -> None:
    """Auto-register existing trace_*.jsonl files that are not yet in the manifest."""
    # Normalize separators so manifests written by older Windows builds (with `\`) still match.
    known_files: set[str] = {
        f.replace("\\", "/") for entry in manifest.get("traces", []) for f in entry.get("files", [])
    }

    for jsonl in sorted(output_dir.glob("**/trace_*.jsonl")):
        rel = _rel_posix(jsonl, output_dir)
        if rel in known_files or jsonl.name in known_files:
            continue
        stem = jsonl.stem
        ts = stem.replace("trace_", "", 1)
        # Prefix with date dir if present
        if jsonl.parent != output_dir:
            ts = jsonl.parent.name.replace("-", "") + "_" + ts
        files = [rel]
        for suffix in [".log", ".html"]:
            companion = jsonl.with_suffix(suffix)
            if companion.exists():
                files.append(_rel_posix(companion, output_dir))
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

    if len(sys.argv) > 1 and sys.argv[1] == "dashboard":
        args = parse_dashboard_args(sys.argv[2:])
        try:
            code = asyncio.run(dashboard_main(args))
        except KeyboardInterrupt:
            code = 0
        sys.exit(code)

    args = parse_args()
    try:
        code = asyncio.run(async_main(args))
    except KeyboardInterrupt:
        code = 0
    sys.exit(code)
