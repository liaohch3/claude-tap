"""CLI entry points for claude-tap."""

from __future__ import annotations

import argparse
import asyncio
import logging

# Keep the stdlib module object available as claude_tap.cli.shutil for
# existing tests and private integrations that monkeypatch shutil.which there.
import shutil
import sys
import threading
import time
import webbrowser
from pathlib import Path

import aiohttp
from aiohttp import web

from claude_tap.certs import CertificateAuthority, ensure_ca, is_macos_ca_trusted, trust_macos_ca
from claude_tap.cli_clients import (
    _CODEX_CHATGPT_TARGET,
    CLIENT_CONFIGS,
    TARGET_DETECTORS,
    ClientConfig,
    _codex_config_override_value,
    _codex_config_override_values,
    _codex_home,
    _codex_profile_arg,
    _codex_selected_provider_base_url_key,
    _detect_claude_target,
    _detect_codebuddy_target,
    _detect_codex_target,
    _extend_no_proxy,
    _has_config_override,
    _has_settings_arg,
    _maybe_rewrite_hermes_gateway_start,
    _read_codebuddy_endpoint_cache,
    _read_codex_config,
    _read_settings_env_base_url,
    _reverse_proxy_trace_options,
    _selected_codex_provider_base_url,
    _settings_arg,
    _toml_dotted_key_segment,
    run_client,
)
from claude_tap.cli_update import (
    _build_update_command,
    _check_pypi_version,
    _detect_installer,
    _start_background_update,
    _version_tuple,
    parse_update_args,
    update_main,
)
from claude_tap.cursor_transcript import import_cursor_transcripts
from claude_tap.forward_proxy import ForwardProxyServer
from claude_tap.history import cleanup_trace_sessions, migrate_legacy_traces
from claude_tap.live import LiveViewerServer
from claude_tap.proxy import proxy_handler
from claude_tap.shared_dashboard import (
    DEFAULT_DASHBOARD_PORT,
    dashboard_url,
    ensure_shared_dashboard,
    is_dashboard_healthy,
    is_legacy_dashboard_healthy,
    resolve_dashboard_port,
)
from claude_tap.trace import TraceWriter
from claude_tap.trace_log_handler import SQLiteLogHandler
from claude_tap.trace_store import get_trace_store, resolve_db_path

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

_CLI_COMPAT_EXPORTS = (
    shutil,
    ClientConfig,
    _CODEX_CHATGPT_TARGET,
    _build_update_command,
    _codex_config_override_value,
    _codex_config_override_values,
    _codex_home,
    _codex_profile_arg,
    _codex_selected_provider_base_url_key,
    _detect_claude_target,
    _detect_codebuddy_target,
    _detect_installer,
    _extend_no_proxy,
    _has_config_override,
    _has_settings_arg,
    _maybe_rewrite_hermes_gateway_start,
    _read_codebuddy_endpoint_cache,
    _read_codex_config,
    _read_settings_env_base_url,
    _selected_codex_provider_base_url,
    _settings_arg,
    _toml_dotted_key_segment,
    parse_update_args,
)


def _open_browser(url: str) -> None:
    """Open URL in browser without blocking. Silently ignores failures in headless environments."""
    threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()


async def _is_dashboard_reusable(host: str, port: int) -> bool:
    return await is_dashboard_healthy(host, port) or await is_legacy_dashboard_healthy(host, port)


_CLAUDE_EXECUTABLE_NAMES = {"claude", "claude.exe", "claude.cmd", "claude.bat"}


def _looks_like_claude_binary_path(value: str) -> bool:
    if not value or value.startswith("-"):
        return False
    # VSCode's claudeProcessWrapper passes the bundled Claude binary path as
    # argv[0]. Require a path-looking file so normal prompts/dirs named
    # "claude" are not silently stripped or executed.
    if "/" not in value and "\\" not in value and not (len(value) > 1 and value[1] == ":"):
        return False
    path = Path(value)
    return path.name.lower() in _CLAUDE_EXECUTABLE_NAMES and path.is_file()


def _extract_wrapped_client_command(client: str, args: list[str]) -> tuple[str | None, list[str]]:
    if client != "claude" or not args:
        return None, args
    if _looks_like_claude_binary_path(args[0]):
        return args[0], args[1:]
    return None, args


def _trust_ca_for_current_user(ca_cert_path: Path) -> int:
    """Trust the forward-proxy CA in the current user's macOS login keychain."""
    if sys.platform != "darwin":
        print("--tap-trust-ca is currently only supported on macOS.", file=sys.stderr)
        print(f"CA certificate: {ca_cert_path}", file=sys.stderr)
        return 1

    if is_macos_ca_trusted(ca_cert_path):
        print(f"🔐 CA already trusted in the macOS login keychain: {ca_cert_path}")
        return 0

    result = trust_macos_ca(ca_cert_path)
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        print("Error: failed to trust claude-tap CA in the macOS login keychain.", file=sys.stderr)
        if details:
            print(details, file=sys.stderr)
        print("This command does not use sudo; macOS may require unlocking your login keychain.", file=sys.stderr)
        return result.returncode or 1

    if not is_macos_ca_trusted(ca_cert_path):
        print("Error: macOS did not report the claude-tap CA as trusted after installation.", file=sys.stderr)
        print(f"CA certificate: {ca_cert_path}", file=sys.stderr)
        return 1

    print(f"🔐 Trusted claude-tap CA in the current user's macOS login keychain: {ca_cert_path}")
    return 0


def _ensure_ca_trust_for_forward_proxy(args: argparse.Namespace, ca_cert_path: Path) -> int:
    """Ensure CA trust when forward-proxy clients need macOS keychain trust."""
    if args.proxy_mode != "forward":
        return 0

    if args.trust_ca:
        return _trust_ca_for_current_user(ca_cert_path)

    cfg = CLIENT_CONFIGS[args.client]
    if sys.platform != "darwin" or not cfg.auto_trust_ca_macos:
        return 0

    if is_macos_ca_trusted(ca_cert_path):
        return 0

    print(f"🔐 {cfg.label} needs the claude-tap CA trusted in your macOS login keychain.")
    print("   Installing for the current user only; no sudo or System keychain write is used.")
    return _trust_ca_for_current_user(ca_cert_path)


async def async_main(args: argparse.Namespace):
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if not args.live_viewer:
        migrate_legacy_traces(output_dir)

    store = get_trace_store()
    trace_metadata = {"client": args.client, "proxy_mode": args.proxy_mode}

    ca_cert_path: Path | None = None
    ca_key_path: Path | None = None
    if args.proxy_mode == "forward":
        ca_cert_path, ca_key_path = ensure_ca()
        trust_result = _ensure_ca_trust_for_forward_proxy(args, ca_cert_path)
        if trust_result != 0:
            return trust_result

    session_id = store.create_session(client=args.client, proxy_mode=args.proxy_mode)

    # Ensure the shared dashboard is running (one port for all sessions).
    dashboard_url_value: str | None = None
    if args.live_viewer:
        dashboard_host = args.host
        dashboard_port = resolve_dashboard_port(args.live_port)
        try:
            dashboard_url_value, spawned = await ensure_shared_dashboard(
                host=dashboard_host,
                port=dashboard_port,
                output_dir=output_dir,
                open_browser=args.open_viewer,
                open_browser_fn=_open_browser,
            )
            if spawned:
                print(f"🌐 Dashboard: {dashboard_url_value}")
            else:
                print(f"🌐 Dashboard: {dashboard_url_value} (shared)")
        except RuntimeError as exc:
            print(f"⚠️  {exc}", file=sys.stderr)

    writer = TraceWriter(session_id, live_server=None, metadata=trace_metadata, store=store)

    # Proxy logs go to SQLite, not terminal (avoids polluting Claude TUI)
    sqlite_handler = SQLiteLogHandler(session_id, store=store)
    sqlite_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
    log.addHandler(sqlite_handler)
    log.setLevel(logging.DEBUG)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    aiohttp_server_log = logging.getLogger("aiohttp.server")
    aiohttp_server_log.addHandler(sqlite_handler)
    aiohttp_server_log.propagate = False
    asyncio_log = logging.getLogger("asyncio")
    asyncio_log.addHandler(sqlite_handler)
    asyncio_log.propagate = False

    # Honor system proxy env (HTTP_PROXY/HTTPS_PROXY/ALL_PROXY/NO_PROXY) for
    # outbound upstream requests. This is important when users route traffic
    # through tools like Clash/VPN.
    session = aiohttp.ClientSession(auto_decompress=False, trust_env=True)

    # Forward proxy mode: raw TCP server with CONNECT/TLS termination
    # Reverse proxy mode: aiohttp web app (current behavior)
    forward_server: ForwardProxyServer | None = None
    runner: web.AppRunner | None = None
    exit_code = 0
    client_started_at = time.time()
    try:
        if args.proxy_mode == "forward":
            assert ca_cert_path is not None
            assert ca_key_path is not None
            ca = CertificateAuthority(ca_cert_path, ca_key_path)
            forward_server = ForwardProxyServer(
                host=args.host,
                port=args.port,
                ca=ca,
                writer=writer,
                session=session,
                local_reverse_target=args.target,
                local_reverse_allowed_path_prefixes=CLIENT_CONFIGS[args.client].forward_base_url_allowed_path_prefixes,
                store_stream_events=args.store_stream_events,
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
                "extra_allowed_path_prefixes": tuple(args.extra_allowed_paths),
                "store_stream_events": args.store_stream_events,
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

        print(f"📁 Trace session: {session_id}")
        print(f"🗄️  Trace database: {resolve_db_path()}")

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

        if not args.no_launch:
            client_started_at = time.time()
            try:
                exit_code = await run_client(
                    actual_port,
                    args.claude_args,
                    client=args.client,
                    proxy_mode=args.proxy_mode,
                    ca_cert_path=ca_cert_path,
                    client_cmd=getattr(args, "client_cmd", None),
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

        # Shared dashboard runs in a detached process; nothing to stop here.
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

        writer.close()

        if args.max_traces > 0:
            cleaned = cleanup_trace_sessions(args.max_traces, protected_session_id=session_id)
            if cleaned:
                print(f"\n🧹 Cleaned up {cleaned} old trace session(s)")

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

        print(f"   Session: {session_id}")
        print(f"   Database: {resolve_db_path()}")
        if dashboard_url_value:
            print(f"   Dashboard: {dashboard_url_value}")

    return exit_code


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse argv, extracting ``--tap-*`` flags for ourselves and forwarding
    everything else to the selected client.
    """
    if argv is None:
        argv = sys.argv[1:]

    tap_parser = argparse.ArgumentParser(
        prog="claude-tap",
        description=(
            "Trace Claude Code, Codex CLI, Gemini CLI, Kimi CLI, OpenCode, Pi, Hermes Agent, "
            "Cursor CLI, Qoder CLI, Antigravity CLI, or CodeBuddy CLI API requests via a local proxy. All flags not listed below are "
            "forwarded to the selected client."
        ),
        epilog=(
            "claude code:\n"
            "  claude-tap                            Basic tracing with live viewer enabled by default\n"
            "  claude-tap --tap-no-live              Disable live viewer server/browser auto-open\n"
            "  claude-tap --tap-no-open              Keep viewers from auto-opening in a browser\n"
            "  claude-tap -- --model claude-opus-4-6  Pass flags to Claude Code\n"
            "  claude-tap -- -c                      Continue last conversation\n"
            "  claude-tap -- --dangerously-skip-permissions  Auto-accept tool calls\n"
            "  claude-tap -- --dangerously-skip-permissions --model claude-sonnet-4-6\n"
            "\n"
            "codex cli:\n"
            "  # Target is auto-detected from Codex auth state when possible\n"
            "  claude-tap --tap-client codex\n"
            "  # If auto-detection cannot read Codex auth, specify OAuth target explicitly\n"
            "  claude-tap --tap-client codex --tap-target https://chatgpt.com/backend-api/codex\n"
            "  # With model and full auto-approval\n"
            "  claude-tap --tap-client codex -- --model codex-mini-latest --full-auto\n"
            "\n"
            "kimi cli:\n"
            "  # Uses KIMI_BASE_URL and forwards to Kimi Code by default\n"
            "  claude-tap --tap-client kimi\n"
            "  claude-tap --tap-client kimi -- --thinking\n"
            "  # Use Moonshot Open Platform instead of Kimi Code\n"
            "  claude-tap --tap-client kimi --tap-target https://api.moonshot.ai/v1\n"
            "\n"
            "gemini cli (defaults to forward proxy mode):\n"
            '  claude-tap --tap-client gemini -- -p "hello"\n'
            "  # Reverse mode sets GOOGLE_GEMINI_BASE_URL and GOOGLE_VERTEX_BASE_URL\n"
            "  claude-tap --tap-client gemini --tap-proxy-mode reverse\n"
            "\n"
            "opencode (multi-provider; defaults to forward proxy mode):\n"
            "  # Forward proxy captures every provider opencode talks to\n"
            "  claude-tap --tap-client opencode\n"
            "  # Force reverse mode (single ANTHROPIC_BASE_URL provider only)\n"
            "  claude-tap --tap-client opencode --tap-proxy-mode reverse\n"
            "\n"
            "pi (multi-provider; defaults to forward proxy mode):\n"
            "  # Forward proxy captures OpenAI Codex OAuth and other providers\n"
            '  claude-tap --tap-client pi -- --model openai-codex/gpt-5.3-codex-spark -p "hello"\n'
            "  # Pi OAuth is configured with /login inside pi, or via PI_CODING_AGENT_DIR\n"
            "\n"
            "hermes agent (multi-provider Python agent — forward proxy default):\n"
            "  # Interactive TUI — captures LLM calls directly\n"
            "  claude-tap --tap-client hermes\n"
            "  # Gateway mode — captures LLM calls triggered by Slack/Telegram/etc. messages\n"
            "  #   (requires messaging platform configured in ~/.hermes/.env)\n"
            "  claude-tap --tap-client hermes -- gateway start\n"
            "\n"
            "cursor cli (defaults to forward proxy mode):\n"
            '  claude-tap --tap-client cursor -- -p --trust --model auto "hello"\n'
            "  # Cursor readable messages are imported from local transcripts after exit\n"
            "\n"
            "qoder cli (defaults to forward proxy mode):\n"
            '  claude-tap --tap-client qoder -- -p "hello" --permission-mode dont_ask\n'
            "  # Authenticate first with `qodercli login` or QODER_PERSONAL_ACCESS_TOKEN / QODER_JOB_TOKEN\n"
            "\n"
            "antigravity cli (defaults to forward proxy mode):\n"
            "  # On macOS, claude-tap auto-trusts the local CA in your user login keychain without sudo\n"
            "  claude-tap --tap-client agy --tap-live\n"
            "\n"
            "codebuddy (reverse proxy mode):\n"
            "  # Auto-detects the endpoint from CodeBuddy's own login cache,\n"
            "  # so internal, iOA, and external users all work out of the box.\n"
            "  claude-tap --tap-client codebuddy\n"
            "  # Or override explicitly (custom/staging deployments)\n"
            "  claude-tap --tap-client codebuddy --tap-target https://www.codebuddy.ai/v2\n"
            '  CODEBUDDY_BASE_URL=https://your-host/v2 claude-tap --tap-client codebuddy -- -p "Reply OK"\n'
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
            "update:\n"
            "  claude-tap update                          Upgrade claude-tap in place\n"
            "  claude-tap update --installer pip          Force pip-based upgrade\n"
            "\n"
            "dashboard:\n"
            "  claude-tap dashboard                       Browse trace history\n"
            "  claude-tap dashboard --tap-live-port 3000  Use a fixed dashboard port\n"
            "\n"
            "trust local CA:\n"
            "  claude-tap trust-ca                        Trust forward-proxy CA in macOS user keychain\n"
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
        choices=sorted(CLIENT_CONFIGS.keys()),
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
            "Default depends on the client: 'reverse' for claude/codex/kimi/codebuddy, "
            "'forward' for agy/gemini/opencode/pi/hermes/cursor/qoder."
        ),
    )
    proxy_group.add_argument(
        "--tap-trust-ca",
        action="store_true",
        dest="trust_ca",
        help=(
            "On macOS, explicitly trust the forward-proxy CA in the current user's login keychain before launch "
            "(no sudo; agy does this automatically when needed)"
        ),
    )
    proxy_group.add_argument(
        "--tap-no-launch", action="store_true", dest="no_launch", help="Only start the proxy, don't launch client"
    )
    proxy_group.add_argument(
        "--tap-allow-path",
        action="append",
        default=[],
        dest="extra_allowed_paths",
        metavar="PREFIX",
        help="Extra path prefix to allow through the proxy (can be repeated, e.g. --tap-allow-path /custom/api)",
    )

    # -- Viewer options --
    viewer_group = tap_parser.add_argument_group("viewer options")
    viewer_group.add_argument(
        "--tap-no-open",
        action="store_false",
        dest="open_viewer",
        default=True,
        help="Don't auto-open live or generated HTML viewers in a browser",
    )
    viewer_group.add_argument(
        "--tap-live",
        action="store_true",
        dest="live_viewer",
        default=True,
        help="Use the shared local dashboard while the client runs (default: on)",
    )
    viewer_group.add_argument(
        "--tap-no-live",
        action="store_false",
        dest="live_viewer",
        help="Disable the shared dashboard (restores pre-v0.1.75 behavior)",
    )
    viewer_group.add_argument(
        "--tap-live-port",
        type=int,
        default=0,
        dest="live_port",
        help=f"Port for the shared dashboard (default: {DEFAULT_DASHBOARD_PORT})",
    )

    # -- Storage & update options --
    storage_group = tap_parser.add_argument_group("storage and update options")
    storage_group.add_argument(
        "--tap-output-dir",
        default="./.traces",
        dest="output_dir",
        help="Legacy trace directory to import once (default: ./.traces)",
    )
    storage_group.add_argument(
        "--tap-max-traces",
        type=int,
        default=50,
        dest="max_traces",
        help="Max trace sessions to keep (default: 50, 0 = unlimited)",
    )
    storage_group.add_argument(
        "--tap-store-stream-events",
        action="store_true",
        dest="store_stream_events",
        help="Persist raw SSE/WebSocket stream events in trace storage and viewer/export output (default: off)",
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
    args.client_cmd, claude_args = _extract_wrapped_client_command(args.client, claude_args)
    args.claude_args = claude_args
    # Default host: 0.0.0.0 in --tap-no-launch mode (proxy-only, typically remote),
    # 127.0.0.1 otherwise (launching the client locally).
    if args.host is None:
        args.host = "0.0.0.0" if args.no_launch else "127.0.0.1"
    if args.target is None:
        if args.client == "codex":
            args.target = _detect_codex_target(claude_args)
        else:
            detector = TARGET_DETECTORS.get(args.client)
            args.target = detector() if detector else CLIENT_CONFIGS[args.client].default_target
    if args.proxy_mode is None:
        args.proxy_mode = CLIENT_CONFIGS[args.client].default_proxy_mode
    if args.trust_ca and args.proxy_mode != "forward":
        tap_parser.error("--tap-trust-ca only applies to forward proxy mode")

    # Validate --tap-allow-path prefixes
    for prefix in args.extra_allowed_paths:
        if not prefix:
            tap_parser.error("--tap-allow-path cannot be empty")
        if not prefix.startswith("/"):
            tap_parser.error(f"--tap-allow-path '{prefix}' must start with '/'")
        if prefix == "/":
            tap_parser.error("--tap-allow-path '/' is too broad and not allowed")
        if prefix.endswith("/"):
            tap_parser.error(f"--tap-allow-path '{prefix}' must not end with '/' (specify exact prefix)")

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
        help="Legacy trace directory to import once (default: ./.traces)",
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

    host = args.host
    port = resolve_dashboard_port(args.live_port)
    if await _is_dashboard_reusable(host, port):
        migrate_legacy_traces(output_dir)
        url = dashboard_url(host, port)
        print(f"🌐 claude-tap dashboard already running: {url}")
        print(f"🗄️  Trace database: {resolve_db_path()}")
        if args.open_viewer:
            _open_browser(url)
        return 0

    server = LiveViewerServer(
        port=port,
        host=host,
        migrate_from=output_dir,
        dashboard_mode=True,
    )
    try:
        await server.start()
    except OSError:
        if await _is_dashboard_reusable(host, port):
            migrate_legacy_traces(output_dir)
            url = dashboard_url(host, port)
            print(f"🌐 claude-tap dashboard already running: {url}")
            if args.open_viewer:
                _open_browser(url)
            return 0
        raise
    print(f"🌐 claude-tap dashboard: {server.url}")
    print(f"🗄️  Trace database: {resolve_db_path()}")
    if output_dir.exists():
        print(f"📁 Legacy import dir: {output_dir}")
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
def parse_trust_ca_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse arguments for the trust-ca subcommand."""
    parser = argparse.ArgumentParser(
        prog="claude-tap trust-ca",
        description=(
            "Trust the claude-tap forward-proxy CA in the current user's macOS login keychain. "
            "This does not use sudo or the System keychain."
        ),
    )
    return parser.parse_args(argv)


def trust_ca_main(argv: list[str] | None = None) -> int:
    """Entry point for the trust-ca subcommand."""
    parse_trust_ca_args(argv)
    ca_cert_path, _ = ensure_ca()
    return _trust_ca_for_current_user(ca_cert_path)


def main_entry() -> None:
    """Entry point for the claude-tap CLI."""
    # Check if first argument is "export" subcommand
    if len(sys.argv) > 1 and sys.argv[1] == "export":
        from claude_tap.export import export_main

        sys.exit(export_main(sys.argv[2:]))

    if len(sys.argv) > 1 and sys.argv[1] == "update":
        sys.exit(update_main(sys.argv[2:]))

    if len(sys.argv) > 1 and sys.argv[1] == "trust-ca":
        sys.exit(trust_ca_main(sys.argv[2:]))

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
