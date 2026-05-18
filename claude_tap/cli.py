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
from datetime import datetime
from pathlib import Path

import aiohttp
from aiohttp import web

from claude_tap.certs import CertificateAuthority, ensure_ca, is_macos_ca_trusted, trust_macos_ca
from claude_tap.cursor_transcript import import_cursor_transcripts
from claude_tap.forward_proxy import ForwardProxyServer
from claude_tap.history import _cleanup_traces, _register_trace, _rel_posix
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
    extra_base_url_envs: tuple[str, ...] = ()
    nesting_env_keys: tuple[str, ...] = ()  # env vars to clear before launch
    # Some CLIs need process env duplicated into a CLI settings payload.
    inject_settings_env: bool = False
    # Some CLIs need a base URL in both env and a native config override.
    base_url_config_key: str | None = None
    # Reverse proxy URL normalization. Example: Codex OAuth receives /v1/* but
    # its upstream target already points at a /codex backend that expects /*.
    strip_path_prefix: str = ""
    strip_path_prefix_unless_target_contains: tuple[str, ...] = ()
    # Default proxy mode when --tap-proxy-mode is not explicitly set.
    # Multi-provider clients (e.g. hermes, opencode, pi) default to "forward" so that all
    # provider traffic is captured regardless of which env var the client honors.
    default_proxy_mode: str = "reverse"
    # Some non-Python/non-Node macOS clients do not honor per-process CA env
    # variables, so they need the forward-proxy CA in the user login keychain.
    auto_trust_ca_macos: bool = False
    # Some clients honor a native provider URL for the core model API but ignore
    # HTTPS_PROXY for that API. In forward mode, point those env vars back at the
    # local proxy and let the forward proxy bridge selected paths to target.
    forward_base_url_envs: tuple[str, ...] = ()
    forward_base_url_allowed_path_prefixes: tuple[str, ...] = ()

    @property
    def missing_help(self) -> str:
        return (
            f"\nError: '{self.cmd}' command not found in PATH.\nPlease install {self.label} first: {self.install_url}\n"
        )

    def reverse_base_url(self, port: int) -> str:
        return f"http://127.0.0.1:{port}{self.base_url_suffix}"

    @property
    def reverse_base_url_envs(self) -> tuple[str, ...]:
        seen: set[str] = set()
        env_keys: list[str] = []
        for env_key in (self.base_url_env, *self.extra_base_url_envs):
            if env_key in seen:
                continue
            seen.add(env_key)
            env_keys.append(env_key)
        return tuple(env_keys)

    def reverse_base_url_env_map(self, port: int) -> dict[str, str]:
        base_url = self.reverse_base_url(port)
        return {env_key: base_url for env_key in self.reverse_base_url_envs}

    def reverse_strip_path_prefix(self, target: str) -> str:
        if not self.strip_path_prefix:
            return ""
        if any(marker in target for marker in self.strip_path_prefix_unless_target_contains):
            return ""
        return self.strip_path_prefix


CLIENT_CONFIGS: dict[str, ClientConfig] = {
    "claude": ClientConfig(
        cmd="claude",
        label="Claude Code",
        install_url="https://docs.anthropic.com/en/docs/claude-code",
        base_url_env="ANTHROPIC_BASE_URL",
        base_url_suffix="",
        default_target="https://api.anthropic.com",
        nesting_env_keys=("CLAUDECODE", "CLAUDE_CODE_SSE_PORT"),
        inject_settings_env=True,
    ),
    "codex": ClientConfig(
        cmd="codex",
        label="Codex CLI",
        install_url="https://github.com/openai/codex",
        base_url_env="OPENAI_BASE_URL",
        base_url_suffix="/v1",
        default_target="https://api.openai.com",
        base_url_config_key="openai_base_url",
        strip_path_prefix="/v1",
        strip_path_prefix_unless_target_contains=("api.openai.com",),
    ),
    "kimi": ClientConfig(
        cmd="kimi",
        label="Kimi Code CLI",
        install_url="https://github.com/MoonshotAI/kimi-cli",
        base_url_env="KIMI_BASE_URL",
        base_url_suffix="",
        default_target="https://api.kimi.com/coding/v1",
    ),
    "gemini": ClientConfig(
        cmd="gemini",
        label="Gemini CLI",
        install_url="https://github.com/google-gemini/gemini-cli",
        base_url_env="GOOGLE_GEMINI_BASE_URL",
        extra_base_url_envs=("GOOGLE_VERTEX_BASE_URL",),
        base_url_suffix="",
        default_target="https://generativelanguage.googleapis.com",
        # Google OAuth / Code Assist traffic spans several Google endpoints.
        # Forward mode captures that flow without assuming a single base URL.
        default_proxy_mode="forward",
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
    "pi": ClientConfig(
        cmd="pi",
        label="Pi",
        install_url="https://github.com/badlogic/pi-mono/tree/main/packages/coding-agent",
        # Pi is multi-provider and stores provider base URLs in its model
        # registry/models.json rather than a single global env var. Reverse
        # mode remains structurally available for custom OpenAI-compatible
        # setups, but forward mode is the reliable default.
        base_url_env="OPENAI_BASE_URL",
        base_url_suffix="/v1",
        default_target="https://api.openai.com",
        default_proxy_mode="forward",
    ),
    "hermes": ClientConfig(
        cmd="hermes",
        label="Hermes Agent",
        install_url="https://github.com/NousResearch/hermes-agent",
        base_url_env="OPENAI_BASE_URL",
        base_url_suffix="/v1",
        default_target="https://api.openai.com",
        # hermes is a Python 3.11+ multi-provider agent; reverse mode requires
        # a user-configured OpenAI-compatible provider in ~/.hermes that honors
        # OPENAI_BASE_URL. Default to forward proxy capture.
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
    "qoder": ClientConfig(
        cmd="qodercli",
        label="Qoder CLI",
        install_url="https://qoder.com/cli",
        # Qoder CLI talks to multiple Qoder endpoints and does not expose a
        # reliable single-provider base URL override. Keep reverse-mode fields
        # structurally valid, but default to forward proxy mode.
        base_url_env="QODER_BASE_URL",
        base_url_suffix="",
        default_target="https://api2.qoder.sh",
        default_proxy_mode="forward",
    ),
    "agy": ClientConfig(
        cmd="agy",
        label="Antigravity CLI",
        install_url="https://antigravity.google/product/antigravity-cli",
        base_url_env="CLOUD_CODE_URL",
        base_url_suffix="",
        default_target="https://daily-cloudcode-pa.googleapis.com",
        default_proxy_mode="forward",
        auto_trust_ca_macos=True,
        forward_base_url_envs=("CLOUD_CODE_URL",),
        forward_base_url_allowed_path_prefixes=("/v1internal",),
    ),
    "codebuddy": ClientConfig(
        cmd="codebuddy",
        label="CodeBuddy",
        install_url="https://www.codebuddy.ai/docs/cli",
        base_url_env="CODEBUDDY_BASE_URL",
        base_url_suffix="",
        # CodeBuddy's bundled OpenAI client appends ``/v2`` to its product
        # endpoint, so the reverse-proxy upstream must include that prefix
        # to hit ``/v2/chat/completions`` rather than the nginx default page.
        # Users on non-Tencent deployments can override via ``--tap-target``
        # or ``CODEBUDDY_BASE_URL``.
        default_target="https://copilot.tencent.com/v2",
        inject_settings_env=True,
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
    cmd_args = _maybe_rewrite_hermes_gateway_start(client, cmd_args)
    has_base_url_config_override = bool(
        cfg.base_url_config_key and _has_config_override(cmd_args, cfg.base_url_config_key)
    )

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
        forward_base_url = cfg.reverse_base_url(port)
        for env_key in cfg.forward_base_url_envs:
            env[env_key] = forward_base_url
        if ca_cert_path:
            env["NODE_EXTRA_CA_CERTS"] = str(ca_cert_path)
            # Codex is a Rust binary; NODE_EXTRA_CA_CERTS does not affect its TLS stack.
            env["SSL_CERT_FILE"] = str(ca_cert_path)
            env["CODEX_CA_CERTIFICATE"] = str(ca_cert_path)
            # hermes is Python (httpx + requests); SSL_CERT_FILE covers httpx,
            # REQUESTS_CA_BUNDLE covers the requests library.
            env["REQUESTS_CA_BUNDLE"] = str(ca_cert_path)

        if cfg.inject_settings_env:
            if not _has_settings_arg(cmd_args):
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
                cmd_args = _settings_arg(settings_payload["env"]) + cmd_args
        # Don't set reverse-mode provider-specific base URL in forward mode.
    else:
        reverse_env = cfg.reverse_base_url_env_map(port)
        env.update(reverse_env)
        env["NO_PROXY"] = "127.0.0.1"
        if cfg.inject_settings_env and not _has_settings_arg(cmd_args):
            cmd_args = _settings_arg(reverse_env) + cmd_args
        if cfg.base_url_config_key and not has_base_url_config_override:
            # Some clients ignore their base URL env in selected auth/transport modes
            # unless the same value is also supplied as a config override.
            base_url = cfg.reverse_base_url(port)
            cmd_args = ["-c", f'{cfg.base_url_config_key}="{base_url}"'] + cmd_args

    for key in cfg.nesting_env_keys:
        env.pop(key, None)

    cmd = [resolved_cmd] + cmd_args
    print(f"\n🚀 Starting {cfg.label}: {' '.join([cfg.cmd, *cmd_args])}")
    if proxy_mode == "forward":
        print(f"   HTTPS_PROXY=http://127.0.0.1:{port}")
        for env_key in cfg.forward_base_url_envs:
            print(f"   {env_key}={cfg.reverse_base_url(port)}")
        if ca_cert_path:
            print(f"   NODE_EXTRA_CA_CERTS={ca_cert_path}")
    else:
        for env_key, base_url in cfg.reverse_base_url_env_map(port).items():
            print(f"   {env_key}={base_url}")
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


_HERMES_GLOBAL_OPTS_WITH_VALUE = {"--profile", "-p"}
_HERMES_GLOBAL_BOOLEAN_OPTS = {"--ignore-user-config", "--accept-hooks"}


def _maybe_rewrite_hermes_gateway_start(client: str, cmd_args: list[str]) -> list[str]:
    """Rewrite ``hermes [global-opts] gateway start`` to ``... gateway run``.

    Recent hermes versions delegate ``gateway start`` to systemd / launchd,
    which spawn the gateway in a fresh env that does NOT inherit the
    HTTPS_PROXY / CA env we inject — trace capture would silently fail.
    ``gateway run`` is the foreground equivalent (it's exactly what the
    systemd unit's ``ExecStart=`` invokes), so the spawned process is our
    child and inherits the injected env.

    Hermes' CLI shape is ``hermes [global-options] <command> [...]``, so the
    rewrite skips any recognised leading global options before matching
    ``gateway start``.
    """
    if client != "hermes":
        return cmd_args
    i = 0
    while i < len(cmd_args):
        arg = cmd_args[i]
        if arg in _HERMES_GLOBAL_OPTS_WITH_VALUE and i + 1 < len(cmd_args):
            i += 2
            continue
        if "=" in arg and arg.split("=", 1)[0] in _HERMES_GLOBAL_OPTS_WITH_VALUE:
            i += 1
            continue
        if arg in _HERMES_GLOBAL_BOOLEAN_OPTS:
            i += 1
            continue
        break
    if i + 1 < len(cmd_args) and cmd_args[i] == "gateway" and cmd_args[i + 1] == "start":
        print(
            "ℹ️  Rewriting `hermes gateway start` to `hermes gateway run` so the "
            "gateway runs in the foreground under claude-tap. Recent hermes "
            "versions delegate `gateway start` to systemd / launchd, which spawns "
            "the gateway in a fresh env that does NOT inherit the proxy / CA env "
            "we inject — trace capture would silently fail. Pass --tap-no-launch "
            "and start the gateway yourself if you want the daemonised behaviour."
        )
        return cmd_args[:i] + ["gateway", "run"] + cmd_args[i + 2 :]
    return cmd_args


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


def _has_settings_arg(args: list[str]) -> bool:
    return any(arg == "--settings" or arg.startswith("--settings=") for arg in args)


def _settings_arg(env_values: dict[str, str]) -> list[str]:
    settings_payload = {"env": env_values}
    return ["--settings", json.dumps(settings_payload, separators=(",", ":"))]


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

    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H%M%S")
    ts = now.strftime("%Y%m%d_%H%M%S")  # kept for manifest compatibility
    date_dir = output_dir / date_str
    date_dir.mkdir(parents=True, exist_ok=True)
    trace_path = date_dir / f"trace_{time_str}.jsonl"
    log_path = date_dir / f"trace_{time_str}.log"

    ca_cert_path: Path | None = None
    ca_key_path: Path | None = None
    if args.proxy_mode == "forward":
        ca_cert_path, ca_key_path = ensure_ca()
        trust_result = _ensure_ca_trust_for_forward_proxy(args, ca_cert_path)
        if trust_result != 0:
            return trust_result

    # Start live viewer server if requested
    live_server: LiveViewerServer | None = None
    if args.live_viewer:
        live_server = LiveViewerServer(trace_path, port=args.live_port, host=args.host, output_dir=output_dir)
        await live_server.start()
        print(f"🌐 Live viewer: {live_server.url}")
        if args.open_viewer:
            _open_browser(live_server.url)

    trace_metadata = {"client": args.client, "proxy_mode": args.proxy_mode}
    writer = TraceWriter(trace_path, live_server=live_server, metadata=trace_metadata)

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
        _register_trace(output_dir, ts, trace_files, metadata=trace_metadata)
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


def _read_settings_env_base_url(path: Path, env_key: str) -> str | None:
    """Read a provider base URL from a Claude-style settings file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    env = data.get("env")
    if not isinstance(env, dict):
        return None
    value = env.get(env_key)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _detect_claude_target() -> str:
    """Auto-detect the upstream target Claude Code would normally use.

    Claude Code can source ``ANTHROPIC_BASE_URL`` from settings files rather
    than the process environment. Mirror that behavior so reverse proxy mode
    captures custom gateways without forcing users to repeat ``--tap-target``.
    """
    env_target = os.environ.get("ANTHROPIC_BASE_URL", "").strip()
    if env_target:
        return env_target

    env_key = CLIENT_CONFIGS["claude"].base_url_env
    candidate_paths = (
        Path.cwd() / ".claude" / "settings.local.json",
        Path.cwd() / ".claude" / "settings.json",
        Path.home() / ".claude" / "settings.json",
    )
    for path in candidate_paths:
        target = _read_settings_env_base_url(path, env_key)
        if target:
            return target
    return CLIENT_CONFIGS["claude"].default_target


def _reverse_proxy_trace_options(client: str, target: str) -> dict[str, object]:
    cfg = CLIENT_CONFIGS[client]
    return {
        "strip_path_prefix": cfg.reverse_strip_path_prefix(target),
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


def _detect_codebuddy_target() -> str:
    """Auto-detect the upstream target CodeBuddy would normally use.

    Priority:
    1. ``CODEBUDDY_BASE_URL`` env var.
    2. ``settings.json`` env block, searched in this order:
       project-local ``.codebuddy/settings{.local,}.json`` →
       ``${CODEBUDDY_CONFIG_DIR}/settings.json`` (when set) →
       ``~/.codebuddy/settings.json``.
    3. CodeBuddy's endpoint cache written on login (all four login modes).
    4. ``ClientConfig.default_target`` fallback.
    """
    env_target = os.environ.get("CODEBUDDY_BASE_URL", "").strip()
    if env_target:
        return env_target

    env_key = CLIENT_CONFIGS["codebuddy"].base_url_env
    config_dir = os.environ.get("CODEBUDDY_CONFIG_DIR", "").strip()
    candidate_paths: list[Path] = [
        Path.cwd() / ".codebuddy" / "settings.local.json",
        Path.cwd() / ".codebuddy" / "settings.json",
    ]
    if config_dir:
        candidate_paths.append(Path(config_dir) / "settings.json")
    candidate_paths.append(Path.home() / ".codebuddy" / "settings.json")
    for path in candidate_paths:
        target = _read_settings_env_base_url(path, env_key)
        if target:
            return target

    cached = _read_codebuddy_endpoint_cache()
    if cached:
        return cached.rstrip("/") + "/v2"

    return CLIENT_CONFIGS["codebuddy"].default_target


def _read_codebuddy_endpoint_cache() -> str | None:
    """Return the host URL from CodeBuddy's login-time endpoint cache, or None."""
    config_dir = os.environ.get("CODEBUDDY_CONFIG_DIR", "").strip()
    base = Path(config_dir) if config_dir else Path.home() / ".codebuddy"
    # md5("CodeBuddy-Endpoint-Cache") — CodeBuddy's endpointCacheKey constant.
    cache_file = base / "local_storage" / "entry_933d5543e80177622c17a73869c0fad7.info"
    try:
        value = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


TARGET_DETECTORS = {
    "claude": _detect_claude_target,
    "codex": _detect_codex_target,
    "codebuddy": _detect_codebuddy_target,
}


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
        help="Start real-time viewer server while the client runs (default: on)",
    )
    viewer_group.add_argument(
        "--tap-no-live",
        action="store_false",
        dest="live_viewer",
        help="Disable the real-time viewer server (restores pre-v0.1.75 behavior)",
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

    server = LiveViewerServer(
        trace_path, port=args.live_port, host=args.host, output_dir=output_dir, dashboard_mode=True
    )
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
