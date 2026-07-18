"""Client launch and target detection helpers for claude-tap CLI."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import signal
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

_BEDROCK_HOST_RE = re.compile(
    r"(^|\.)("
    r"(bedrock-runtime|bedrock-runtime-fips)"
    r"\.[a-z0-9-]+\.(amazonaws\.com|amazonaws\.com\.cn|vpce\.amazonaws\.com)"
    r"|bedrock-mantle\.[a-z0-9-]+\.(api\.aws|amazonaws\.com|amazonaws\.com\.cn)"
    r")$"
)


def _is_aws_native_bedrock_url(url: str) -> bool:
    """Return True if the URL points to a real AWS Bedrock endpoint (SigV4-signed).

    AWS native Bedrock endpoints match patterns like:
      - bedrock-runtime.us-east-1.amazonaws.com
      - bedrock-runtime-fips.us-west-2.amazonaws.com
      - vpce-xxx.bedrock-runtime.us-east-1.vpce.amazonaws.com
      - bedrock-mantle.us-east-1.api.aws
      - bedrock-mantle.us-east-1.amazonaws.com

    Custom gateways on other AWS services (e.g. API Gateway *.execute-api.*)
    or company proxies do NOT use SigV4, so rewriting their URL is safe.
    """
    try:
        from urllib.parse import urlparse

        host = urlparse(url).hostname or ""
    except Exception:
        return False
    return bool(_BEDROCK_HOST_RE.search(host))


def _is_truthy_env_value(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _is_claude_bedrock_enabled() -> bool:
    return _is_truthy_env_value(_resolve_env_value("CLAUDE_CODE_USE_BEDROCK"))


def _is_claude_vertex_enabled() -> bool:
    return _is_truthy_env_value(_resolve_env_value("CLAUDE_CODE_USE_VERTEX"))


def _should_rewrite_extra_base_url_env(env_key: str) -> bool:
    current_value = _resolve_env_value(env_key)
    if env_key == "ANTHROPIC_BEDROCK_BASE_URL":
        if not _is_claude_bedrock_enabled() or not current_value:
            return False
        return not _is_aws_native_bedrock_url(current_value)
    if env_key == "ANTHROPIC_VERTEX_BASE_URL":
        return _is_claude_vertex_enabled() and bool(current_value)
    return True


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
    # Transcript-only clients are observed from local session logs instead of a
    # spawned process and do not need a reverse or forward proxy.
    transcript_only: bool = False
    # Extra client product endpoints accepted by the reverse proxy. Keep these
    # client-scoped so one client's control-plane routes do not broaden every
    # reverse proxy instance.
    reverse_allowed_path_prefixes: tuple[str, ...] = ()
    # When set, accepted reverse-proxy traffic outside these prefixes is
    # relayed without persistence. This avoids storing account/control-plane
    # responses while preserving the model traffic users launched us to trace.
    reverse_trace_path_prefixes: tuple[str, ...] = ()

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
        env_map: dict[str, str] = {}
        for env_key in self.reverse_base_url_envs:
            if env_key in self.extra_base_url_envs and not _should_rewrite_extra_base_url_env(env_key):
                continue
            env_map[env_key] = base_url
        return env_map

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
        extra_base_url_envs=("ANTHROPIC_BEDROCK_BASE_URL", "ANTHROPIC_VERTEX_BASE_URL"),
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
        strip_path_prefix="/v1",
        strip_path_prefix_unless_target_contains=("api.openai.com",),
    ),
    "grok": ClientConfig(
        cmd="grok",
        label="Grok Build CLI",
        install_url="https://docs.x.ai/build/overview",
        base_url_env="GROK_CLI_CHAT_PROXY_BASE_URL",
        base_url_suffix="/v1",
        default_target="https://cli-chat-proxy.grok.com/v1",
        # Grok sends /v1/* to its configured base URL. The official upstream
        # target already includes /v1, so remove the local prefix when relaying.
        strip_path_prefix="/v1",
        reverse_allowed_path_prefixes=(
            "/v1/user",
            "/v1/settings",
            "/v1/bundle",
            "/v1/subagents",
            "/v1/feedback",
            "/v1/storage",
            "/v1/traces",
            "/v1/deployment",
            "/v1/mcp",
            "/v1/sessions",
            "/v1/billing",
        ),
        reverse_trace_path_prefixes=(
            "/v1/responses",
            "/v1/chat/completions",
            "/v1/storage",
            "/v1/traces",
        ),
    ),
    "codexapp": ClientConfig(
        cmd="codex",
        label="Codex App",
        install_url="https://openai.com/codex",
        base_url_env="CODEX_HOME",
        base_url_suffix="",
        default_target="codex-app://sessions",
        default_proxy_mode="transcript",
        transcript_only=True,
    ),
    "kimi": ClientConfig(
        cmd="kimi",
        label="Kimi Code CLI",
        install_url="https://github.com/MoonshotAI/kimi-cli",
        base_url_env="KIMI_BASE_URL",
        base_url_suffix="",
        default_target="https://api.kimi.com/coding/v1",
    ),
    "kimi-code": ClientConfig(
        cmd="kimi",
        label="Kimi Code CLI",
        install_url="https://github.com/MoonshotAI/kimi-code",
        base_url_env="KIMI_CODE_BASE_URL",
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
    "mimo": ClientConfig(
        cmd="mimo",
        label="MiMo Code",
        install_url="https://mimo.xiaomi.com/en/mimocode",
        # MiMo Code is an OpenCode fork (https://github.com/XiaomiMiMo/MiMo-Code).
        # It inherits the same multi-provider env vars; forward proxy is the
        # natural default to capture all provider traffic transparently.
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
    "openclaw": ClientConfig(
        cmd="openclaw",
        label="OpenClaw",
        install_url="https://github.com/openclaw/openclaw",
        base_url_env="OPENAI_BASE_URL",
        extra_base_url_envs=("ANTHROPIC_BASE_URL", "GOOGLE_GEMINI_BASE_URL", "OPENROUTER_BASE_URL", "CUSTOM_BASE_URL"),
        base_url_suffix="/v1",
        default_target="https://api.openai.com",
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
    client_cmd: str | None = None,
    capture_only: bool = False,
) -> int:
    cfg = CLIENT_CONFIGS[client]

    # asyncio.create_subprocess_exec uses CreateProcess on Windows, which only
    # auto-appends `.exe`; resolve here so npm `.cmd`/`.bat` shims also work.
    display_cmd = client_cmd or cfg.cmd
    resolved_cmd = str(Path(client_cmd)) if client_cmd and Path(client_cmd).is_file() else shutil.which(display_cmd)
    if resolved_cmd is None:
        if client_cmd:
            print(f"\nError: '{client_cmd}' command not found.\nPlease check the wrapper-provided {cfg.label} path.\n")
        else:
            print(cfg.missing_help)
        return 1

    env = os.environ.copy()
    cleanup_paths: list[Path] = []

    cmd_args = list(extra_args)
    cmd_args = _maybe_rewrite_hermes_gateway_start(client, cmd_args)
    kimi_code_sandbox: Path | None = None
    kimi_code_source_home: Path | None = None

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
        if client == "mimo":
            # MiMo defaults to mimo-only mode and ignores provider env vars unless disabled.
            env["MIMOCODE_MIMO_ONLY"] = "false"
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
        if client == "kimi-code":
            kimi_code_sandbox, _patched_providers, kimi_code_source_home, cmd_args = _prepare_kimi_code_reverse_sandbox(
                port, cmd_args
            )
            has_kimi_code_model_arg = bool(_kimi_code_model_arg(cmd_args))
            if has_kimi_code_model_arg:
                env.pop("KIMI_MODEL_NAME", None)
                env.pop("KIMI_MODEL_BASE_URL", None)
            elif not _has_active_kimi_code_model_env():
                env.pop("KIMI_MODEL_BASE_URL", None)
            reverse_env = {
                "KIMI_CODE_HOME": str(kimi_code_sandbox),
                "KIMI_CODE_BASE_URL": cfg.reverse_base_url(port),
                "KIMI_BASE_URL": cfg.reverse_base_url(port),
            }
            if not has_kimi_code_model_arg and _should_proxy_kimi_code_model_env():
                reverse_env["KIMI_MODEL_BASE_URL"] = cfg.reverse_base_url(port)
        elif client == "openclaw":
            reverse_env = _openclaw_reverse_env(port, cmd_args)
        elif capture_only and client in {"hermes", "kimi"}:
            reverse_env = _multi_provider_reverse_env(port)
        elif capture_only and client == "mimo":
            reverse_env = _opencode_reverse_env(port)
            reverse_env["MIMOCODE_MIMO_ONLY"] = "false"
        elif capture_only and client == "opencode":
            reverse_env = _opencode_reverse_env(port)
        elif client == "mimo":
            reverse_env = cfg.reverse_base_url_env_map(port)
            reverse_env["MIMOCODE_MIMO_ONLY"] = "false"
        else:
            reverse_env = cfg.reverse_base_url_env_map(port)
        cleanup_path = reverse_env.pop(_OPENCLAW_CLEANUP_ENV, None)
        if cleanup_path:
            cleanup_paths.append(Path(cleanup_path))
        env.update(reverse_env)
        if client == "mimo":
            # MiMo talks to a local HTTP server in TUI mode; preserve any existing
            # NO_PROXY entries and bypass localhost the same way forward mode does.
            _extend_no_proxy(env, ("localhost", "127.0.0.1", "::1"))
        else:
            env["NO_PROXY"] = "127.0.0.1"
        if cfg.inject_settings_env and not _has_settings_arg(cmd_args):
            cmd_args = _settings_arg(reverse_env) + cmd_args
        if client == "codex":
            cmd_args = _codex_reverse_args(cfg.reverse_base_url(port), cmd_args)

    for key in cfg.nesting_env_keys:
        env.pop(key, None)

    cmd = [resolved_cmd] + cmd_args
    print(f"\n🚀 Starting {cfg.label}: {' '.join([display_cmd, *cmd_args])}")
    if proxy_mode == "forward":
        print(f"   HTTPS_PROXY=http://127.0.0.1:{port}")
        for env_key in cfg.forward_base_url_envs:
            print(f"   {env_key}={cfg.reverse_base_url(port)}")
        if ca_cert_path:
            print(f"   NODE_EXTRA_CA_CERTS={ca_cert_path}")
    elif client == "kimi-code":
        print(f"   KIMI_CODE_HOME={env.get('KIMI_CODE_HOME', '')}")
        print(f"   KIMI_CODE_BASE_URL={env.get('KIMI_CODE_BASE_URL', '')}")
    else:
        for env_key, base_url in cfg.reverse_base_url_env_map(port).items():
            print(f"   {env_key}={base_url}")
    print()

    # Give child its own process group and make it the foreground group
    # so the TUI app has full terminal control (e.g. Cmd+Delete, Ctrl+U).
    use_fg = hasattr(os, "tcsetpgrp") and sys.stdin.isatty()

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            env=env,
            stdin=None,
            stdout=None,
            stderr=None,
            **({"process_group": 0} if use_fg else {}),
        )
    except Exception:
        for path in cleanup_paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        if client == "kimi-code" and proxy_mode == "reverse" and kimi_code_sandbox is not None:
            shutil.rmtree(kimi_code_sandbox, ignore_errors=True)
        raise

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

    try:
        code = await proc.wait()
    finally:
        for path in cleanup_paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        if (
            client == "kimi-code"
            and proxy_mode == "reverse"
            and kimi_code_sandbox is not None
            and kimi_code_source_home is not None
        ):
            _merge_kimi_code_session_index(kimi_code_source_home, kimi_code_sandbox)
            _persist_kimi_code_sandbox(kimi_code_source_home, kimi_code_sandbox)
            _remap_kimi_code_sandbox_paths(kimi_code_source_home, kimi_code_sandbox)
            shutil.rmtree(kimi_code_sandbox, ignore_errors=True)

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
    if "*" in existing:
        env["NO_PROXY"] = "*"
        env["no_proxy"] = "*"
        return

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


def _codex_config_override_values(args: list[str]) -> list[str]:
    values: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("-c", "--config"):
            if i + 1 < len(args):
                values.append(args[i + 1])
            i += 2
            continue
        if arg.startswith("--config="):
            values.append(arg.split("=", 1)[1])
        i += 1
    return values


def _codex_config_override_value(args: list[str] | None, key: str) -> object | None:
    if not args:
        return None
    prefix = f"{key}="
    value: object | None = None
    for override in _codex_config_override_values(args):
        if not override.startswith(prefix):
            continue
        raw = override[len(prefix) :].strip()
        try:
            parsed = tomllib.loads(f"value = {raw}\n")
        except tomllib.TOMLDecodeError:
            value = raw
        else:
            value = parsed.get("value")
    return value


def _codex_profile_arg(args: list[str] | None) -> str | None:
    if not args:
        return None
    profile: str | None = None
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("-p", "--profile"):
            if i + 1 < len(args):
                profile = args[i + 1]
            i += 2
            continue
        if arg.startswith("--profile="):
            profile = arg.split("=", 1)[1]
        i += 1
    return profile.strip() if profile and profile.strip() else None


def _toml_dotted_key_segment(value: str) -> str:
    """Return a TOML dotted-key segment for a Codex config key."""
    if value and value.isascii() and all(char.isalnum() or char in {"_", "-"} for char in value):
        return value
    return json.dumps(value)


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def _read_codex_config_file(config_path: Path) -> dict[str, object]:
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_codex_config() -> dict[str, object]:
    return _read_codex_config_file(_codex_home() / "config.toml")


def _read_codex_profile_config(profile: str | None) -> dict[str, object]:
    if not profile:
        return {}
    return _read_codex_config_file(_codex_home() / f"{profile}.config.toml")


def _selected_codex_provider_base_url(args: list[str] | None = None) -> tuple[str, str] | None:
    """Return the selected custom Codex provider and base URL, if configured."""
    data = _read_codex_config()
    provider = _codex_config_override_value(args, "model_provider")
    profile = _codex_profile_arg(args)
    if profile is None:
        configured_profile = _codex_config_override_value(args, "profile")
        if configured_profile is None:
            configured_profile = data.get("profile")
        if isinstance(configured_profile, str) and configured_profile.strip():
            profile = configured_profile.strip()

    profile_data = _read_codex_profile_config(profile)
    if not isinstance(provider, str):
        provider = profile_data.get("model_provider")

    profiles = data.get("profiles")
    if profile and isinstance(profiles, dict):
        profile_config = profiles.get(profile)
        if isinstance(profile_config, dict) and not isinstance(provider, str):
            provider = profile_config.get("model_provider")

    if not isinstance(provider, str):
        provider = data.get("model_provider")
    if not isinstance(provider, str) or not provider.strip():
        return None
    provider = provider.strip()

    provider_base_url_key = f"model_providers.{_toml_dotted_key_segment(provider)}.base_url"
    base_url_override = _codex_config_override_value(args, provider_base_url_key)
    if isinstance(base_url_override, str) and base_url_override.strip():
        return provider, base_url_override.strip()

    base_url: object | None = None
    for config in (profile_data, data):
        providers = config.get("model_providers")
        if not isinstance(providers, dict):
            continue
        provider_config = providers.get(provider)
        if not isinstance(provider_config, dict):
            continue
        base_url = provider_config.get("base_url")
        if isinstance(base_url, str) and base_url.strip():
            break
    if not isinstance(base_url, str) or not base_url.strip():
        return None
    return provider.strip(), base_url.strip()


def _codex_selected_provider_base_url_key(args: list[str] | None = None) -> str | None:
    selected = _selected_codex_provider_base_url(args)
    if selected is None:
        return None
    provider, _base_url = selected
    return f"model_providers.{_toml_dotted_key_segment(provider)}.base_url"


def _codex_reverse_args(proxy_base_url: str, args: list[str]) -> list[str]:
    """Route Codex through the proxy over HTTP/SSE without changing user config."""
    provider_base_url_key = _codex_selected_provider_base_url_key(args)
    if provider_base_url_key:
        provider_key = provider_base_url_key.removesuffix(".base_url")
        overrides = [
            f'{provider_base_url_key}="{proxy_base_url}"',
            f"{provider_key}.supports_websockets=false",
        ]
        args = _without_config_overrides(args, {provider_base_url_key, f"{provider_key}.supports_websockets"})
    else:
        provider_key = "model_providers.claude-tap-openai"
        overrides = [
            'model_provider="claude-tap-openai"',
            f'{provider_key}.name="claude-tap"',
            f'{provider_key}.base_url="{proxy_base_url}"',
            f'{provider_key}.wire_api="responses"',
            f"{provider_key}.requires_openai_auth=true",
            f"{provider_key}.supports_websockets=false",
        ]
        args = _without_config_overrides(args, {"model_provider"})

    injected = [item for override in overrides for item in ("-c", override)]
    return injected + args


def _without_config_overrides(args: list[str], keys: set[str]) -> list[str]:
    """Remove Codex config overrides that would bypass enforced proxy settings."""
    filtered: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in {"-c", "--config"} and i + 1 < len(args):
            value = args[i + 1]
            if any(value.startswith(f"{key}=") for key in keys):
                i += 2
                continue
            filtered.extend((arg, value))
            i += 2
            continue
        if arg.startswith("--config="):
            value = arg.split("=", 1)[1]
            if any(value.startswith(f"{key}=") for key in keys):
                i += 1
                continue
        filtered.append(arg)
        i += 1
    return filtered


def _has_settings_arg(args: list[str]) -> bool:
    return any(arg == "--settings" or arg.startswith("--settings=") for arg in args)


def _settings_arg(env_values: dict[str, str]) -> list[str]:
    settings_payload = {"env": env_values}
    return ["--settings", json.dumps(settings_payload, separators=(",", ":"))]


_CODEX_CHATGPT_TARGET = "https://chatgpt.com/backend-api/codex"


def _resolve_env_value(env_key: str) -> str:
    """Resolve an env key from process env or Claude settings files."""
    value = os.environ.get(env_key, "").strip()
    if value:
        return value
    candidate_paths = (
        Path.cwd() / ".claude" / "settings.local.json",
        Path.cwd() / ".claude" / "settings.json",
        Path.home() / ".claude" / "settings.json",
    )
    for path in candidate_paths:
        found = _read_settings_env_base_url(path, env_key)
        if found:
            return found
    return ""


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

    Claude Code can source provider base URLs from settings files rather than
    only the process environment. Mirror that behavior for custom Anthropic,
    Bedrock, and Vertex gateways without forcing users to repeat
    ``--tap-target``.
    """
    if _is_claude_vertex_enabled():
        vertex_target = _resolve_env_value("ANTHROPIC_VERTEX_BASE_URL")
    else:
        vertex_target = ""
    if vertex_target:
        return vertex_target

    if _is_claude_bedrock_enabled():
        bedrock_target = _resolve_env_value("ANTHROPIC_BEDROCK_BASE_URL")
    else:
        bedrock_target = ""
    if bedrock_target and not _is_aws_native_bedrock_url(bedrock_target):
        return bedrock_target

    env_target = _resolve_env_value("ANTHROPIC_BASE_URL")
    if env_target:
        return env_target

    return CLIENT_CONFIGS["claude"].default_target


def _reverse_proxy_trace_options(client: str, target: str) -> dict[str, object]:
    cfg = CLIENT_CONFIGS[client]
    return {
        "strip_path_prefix": cfg.reverse_strip_path_prefix(target),
        "force_http": False,
    }


def _detect_codex_target(args: list[str] | None = None) -> str:
    """Auto-detect the correct upstream target for Codex CLI.

    Explicit provider and CLI target configuration takes precedence over the
    auth-mode defaults, matching Codex's own layered config resolution.
    """
    custom_provider = _selected_codex_provider_base_url(args)
    if custom_provider is not None:
        _provider, base_url = custom_provider
        return base_url

    openai_base_url_override = _codex_config_override_value(args, "openai_base_url")
    if isinstance(openai_base_url_override, str) and openai_base_url_override.strip():
        return openai_base_url_override.strip()

    codex_home = _codex_home()
    auth_file = codex_home / "auth.json"
    try:
        data = json.loads(auth_file.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("auth_mode") == "chatgpt":
            return _CODEX_CHATGPT_TARGET
    except (OSError, json.JSONDecodeError, ValueError):
        pass

    env_target = os.environ.get(CLIENT_CONFIGS["codex"].base_url_env, "").strip()
    if env_target:
        return env_target

    data = _read_codex_config()
    openai_base_url = data.get("openai_base_url")
    if isinstance(openai_base_url, str) and openai_base_url.strip():
        return openai_base_url.strip()
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


def _detect_grok_target() -> str:
    """Auto-detect the Grok Build CLI chat proxy endpoint."""
    env_target = os.environ.get(CLIENT_CONFIGS["grok"].base_url_env, "").strip()
    if env_target:
        return env_target
    return CLIENT_CONFIGS["grok"].default_target


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


_KIMI_CODE_MANAGED_PROVIDER = "managed:kimi-code"
_KIMI_CODE_SKIP_MIGRATION_MARKER = ".skip-migration-from-kimi-cli"
_KIMI_CODE_MIGRATED_MARKER = ".migrated-to-kimi-code"
_KIMI_CODE_SANDBOX_DIR_PREFIX = "claude_tap_kimi_code_"


def _kimi_code_home() -> Path:
    return _kimi_code_source_home()


def _kimi_code_source_home() -> Path:
    """Persistent kimi-code data dir used when building a tap sandbox."""
    override = os.environ.get("KIMI_CODE_HOME", "").strip()
    if override and _KIMI_CODE_SANDBOX_DIR_PREFIX not in override:
        return Path(override).expanduser()
    return Path.home() / ".kimi-code"


def _kimi_code_migration_already_handled(real_home: Path) -> bool:
    """Mirror kimi-code detectPendingMigration suppression for the real home."""
    if (real_home / _KIMI_CODE_SKIP_MIGRATION_MARKER).is_file():
        return True
    marker = Path.home() / ".kimi" / _KIMI_CODE_MIGRATED_MARKER
    if not marker.is_file():
        return False
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        # Unreadable marker: kimi-code treats this as "already handled".
        return True
    target_path = data.get("target_path")
    if not isinstance(target_path, str):
        return True
    return Path(target_path).expanduser().resolve() == real_home.resolve()


def _sync_kimi_code_migration_suppression(source_home: Path, sandbox: Path) -> None:
    """Copy or synthesize the skip marker so sandbox startups skip the migrate TUI."""
    skip_source = source_home / _KIMI_CODE_SKIP_MIGRATION_MARKER
    skip_target = sandbox / _KIMI_CODE_SKIP_MIGRATION_MARKER
    if skip_source.is_file():
        shutil.copy2(skip_source, skip_target)
        return
    if _kimi_code_migration_already_handled(source_home):
        skip_target.write_text("", encoding="utf-8")


def _read_kimi_code_config(home: Path | None = None, path: Path | None = None) -> dict[str, object]:
    config_path = path or (home or _kimi_code_home()) / "config.toml"
    try:
        text = config_path.read_text(encoding="utf-8")
        data = json.loads(text) if config_path.suffix.lower() == ".json" else tomllib.loads(text)
    except (OSError, json.JSONDecodeError, tomllib.TOMLDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _kimi_code_option_value(args: Sequence[str], flags: set[str]) -> str | None:
    for idx, arg in enumerate(args):
        if arg in flags and idx + 1 < len(args):
            value = args[idx + 1].strip()
            if value:
                return value
        for flag in flags:
            prefix = f"{flag}="
            if arg.startswith(prefix):
                value = arg[len(prefix) :].strip()
                if value:
                    return value
    return None


def _replace_kimi_code_option_value(args: Sequence[str], flags: set[str], value: str) -> list[str]:
    rewritten: list[str] = []
    skip_next = False
    for arg in args:
        if skip_next:
            rewritten.append(value)
            skip_next = False
            continue
        if arg in flags:
            rewritten.append(arg)
            skip_next = True
            continue
        matched = False
        for flag in flags:
            if arg.startswith(f"{flag}="):
                rewritten.append(f"{flag}={value}")
                matched = True
                break
        if not matched:
            rewritten.append(arg)
    return rewritten


def _kimi_code_model_arg(cmd_args: Sequence[str] = ()) -> str | None:
    return _kimi_code_option_value(cmd_args, {"--model", "-m"})


def _kimi_code_config_file_arg(cmd_args: Sequence[str] = ()) -> str | None:
    return _kimi_code_option_value(cmd_args, {"--config-file"})


def _kimi_code_inline_config_arg(cmd_args: Sequence[str] = ()) -> str | None:
    return _kimi_code_option_value(cmd_args, {"--config"})


def _loads_kimi_code_inline_config(value: str) -> dict[str, object]:
    value = value.strip()
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        try:
            parsed = tomllib.loads(value)
        except (tomllib.TOMLDecodeError, ValueError):
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _kimi_code_config_for_args(cmd_args: Sequence[str] = ()) -> dict[str, object]:
    inline_config = _kimi_code_inline_config_arg(cmd_args)
    if inline_config:
        return _loads_kimi_code_inline_config(inline_config)

    config_file = _kimi_code_config_file_arg(cmd_args)
    if config_file:
        return _read_kimi_code_config(path=Path(config_file).expanduser())

    return _read_kimi_code_config()


def _has_active_kimi_code_model_env() -> bool:
    return bool(os.environ.get("KIMI_MODEL_NAME", "").strip())


def _should_proxy_kimi_code_model_env() -> bool:
    if not _has_active_kimi_code_model_env():
        return False
    if os.environ.get("KIMI_MODEL_BASE_URL", "").strip():
        return True
    provider_type = os.environ.get("KIMI_MODEL_PROVIDER_TYPE", "").strip().lower()
    return provider_type in {"", "kimi"}


def _kimi_code_provider_base_url(provider: dict[str, object]) -> str | None:
    base_url = provider.get("base_url")
    if isinstance(base_url, str) and base_url.strip():
        return base_url.strip()
    env_table = provider.get("env")
    if isinstance(env_table, dict):
        fallback = env_table.get("KIMI_BASE_URL")
        if isinstance(fallback, str) and fallback.strip():
            return fallback.strip()
    return None


def _kimi_code_selected_provider_names(config: dict[str, object], cmd_args: Sequence[str] = ()) -> set[str]:
    providers = config.get("providers")
    if not isinstance(providers, dict):
        return set()

    selected_model = _kimi_code_model_arg(cmd_args)
    if not selected_model:
        default_model = config.get("default_model")
        if isinstance(default_model, str) and default_model.strip():
            selected_model = default_model.strip()

    models = config.get("models")
    if selected_model and isinstance(models, dict):
        model = models.get(selected_model)
        if isinstance(model, dict):
            provider_name = model.get("provider")
            if isinstance(provider_name, str) and provider_name in providers:
                return {provider_name}

    kimi_providers = [
        name
        for name, provider in providers.items()
        if isinstance(name, str) and isinstance(provider, dict) and provider.get("type") == "kimi"
    ]
    if len(kimi_providers) == 1:
        return {kimi_providers[0]}
    if _KIMI_CODE_MANAGED_PROVIDER in kimi_providers:
        return {_KIMI_CODE_MANAGED_PROVIDER}
    return set()


def _collect_kimi_code_provider_urls(config: dict[str, object], provider_names: set[str] | None = None) -> list[str]:
    urls: list[str] = []
    providers = config.get("providers")
    if not isinstance(providers, dict):
        return urls
    for name, provider in providers.items():
        if provider_names is not None and name not in provider_names:
            continue
        if not isinstance(provider, dict) or provider.get("type") != "kimi":
            continue
        base_url = _kimi_code_provider_base_url(provider)
        if base_url:
            urls.append(base_url)
    return urls


def _patch_kimi_code_config_dict(
    config: dict[str, object], proxy_base: str, cmd_args: Sequence[str] = ()
) -> tuple[dict[str, object], list[str]]:
    patched = json.loads(json.dumps(config))
    patched_providers: list[str] = []
    provider_names = _kimi_code_selected_provider_names(config, cmd_args)

    providers = patched.get("providers")
    if isinstance(providers, dict):
        for name, provider in providers.items():
            if not isinstance(provider, dict) or provider.get("type") != "kimi":
                continue
            if provider_names and name not in provider_names:
                continue
            provider["base_url"] = proxy_base
            env_table = provider.get("env")
            if isinstance(env_table, dict) and "KIMI_BASE_URL" in env_table:
                env_table["KIMI_BASE_URL"] = proxy_base
            patched_providers.append(str(name))

    return patched, patched_providers


def _kimi_code_config_url_replacements(
    config: dict[str, object], proxy_base: str, provider_names: set[str]
) -> list[tuple[str, str]]:
    replacements: list[tuple[str, str]] = []
    for old_url in _collect_kimi_code_provider_urls(config, provider_names):
        replacements.append((old_url, proxy_base))
    seen: set[str] = set()
    ordered: list[tuple[str, str]] = []
    for old_url, new_url in sorted(replacements, key=lambda item: len(item[0]), reverse=True):
        if old_url in seen:
            continue
        seen.add(old_url)
        ordered.append((old_url, new_url))
    return ordered


def _replace_kimi_code_toml_url_assignments(text: str, old_url: str, new_url: str) -> str:
    escaped = re.escape(old_url)
    pattern = rf'(?m)^((?:base_url|KIMI_BASE_URL)\s*=\s*["\']){escaped}(["\'].*)$'
    return re.sub(pattern, rf"\1{new_url}\2", text)


def _insert_kimi_code_provider_base_url(text: str, provider_name: str, proxy_base: str) -> str:
    quoted = f'"{re.escape(provider_name)}"'
    bare = re.escape(provider_name)
    pattern = rf"(?m)^(\[providers\.(?:{quoted}|{bare})\]\s*(?:#.*)?\r?\n)"
    replacement = rf'\1base_url = "{proxy_base}"' + "\n"
    return re.sub(pattern, replacement, text, count=1)


def _patch_kimi_code_config_text(
    source_text: str, proxy_base: str, cmd_args: Sequence[str] = ()
) -> tuple[str, list[str]]:
    if not source_text.strip():
        return _minimal_kimi_code_config_toml(proxy_base), [_KIMI_CODE_MANAGED_PROVIDER]
    try:
        config = tomllib.loads(source_text)
    except (tomllib.TOMLDecodeError, ValueError):
        config = {}
    if not isinstance(config, dict):
        config = {}
    _, patched_providers = _patch_kimi_code_config_dict(config, proxy_base, cmd_args)
    provider_names = set(patched_providers)
    result = source_text
    for old_url, new_url in _kimi_code_config_url_replacements(config, proxy_base, provider_names):
        result = _replace_kimi_code_toml_url_assignments(result, old_url, new_url)
    providers = config.get("providers")
    if isinstance(providers, dict):
        for name in provider_names:
            provider = providers.get(name)
            if isinstance(provider, dict) and not _kimi_code_provider_base_url(provider):
                result = _insert_kimi_code_provider_base_url(result, str(name), proxy_base)
    return result, patched_providers


def _patch_kimi_code_inline_config(value: str, proxy_base: str, cmd_args: Sequence[str] = ()) -> str:
    config = _loads_kimi_code_inline_config(value)
    if not config:
        return value
    if value.strip().startswith(("{", "[")):
        patched, _ = _patch_kimi_code_config_dict(config, proxy_base, cmd_args)
        return json.dumps(patched, separators=(",", ":"))
    patched_text, _ = _patch_kimi_code_config_text(value, proxy_base, cmd_args)
    return patched_text


def _minimal_kimi_code_config_toml(proxy_base: str) -> str:
    return (
        'default_model = "kimi-code/kimi-for-coding"\n'
        "\n"
        f'[providers."{_KIMI_CODE_MANAGED_PROVIDER}"]\n'
        'type = "kimi"\n'
        f'base_url = "{proxy_base}"\n'
        'api_key = ""\n'
        "\n"
        '[models."kimi-code/kimi-for-coding"]\n'
        f'provider = "{_KIMI_CODE_MANAGED_PROVIDER}"\n'
        'model = "kimi-for-coding"\n'
        "max_context_size = 262144\n"
    )


def _kimi_code_config_has_launch_state(source_text: str) -> bool:
    if not source_text.strip():
        return False
    try:
        config = tomllib.loads(source_text)
    except (tomllib.TOMLDecodeError, ValueError):
        return True
    if not isinstance(config, dict):
        return False
    return any(key in config for key in ("default_model", "models", "providers"))


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_kimi_code_config_metadata(
    sandbox: Path,
    *,
    source_config: Path | None,
    sandbox_config: Path,
    patched_text: str,
    proxy_base: str,
    upstream_base: str,
) -> None:
    if source_config is None:
        return
    metadata = {
        "source_config": str(source_config.expanduser().resolve()),
        "sandbox_config": str(sandbox_config),
        "patched_sha256": _sha256_text(patched_text),
        "proxy_base": proxy_base,
        "upstream_base": upstream_base,
    }
    (sandbox / _KIMI_CODE_CONFIG_METADATA).write_text(json.dumps(metadata), encoding="utf-8")


def _persist_kimi_code_config_edits(sandbox: Path) -> None:
    metadata_path = sandbox / _KIMI_CODE_CONFIG_METADATA
    if not metadata_path.is_file():
        return
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    if not isinstance(metadata, dict):
        return
    source_config_raw = metadata.get("source_config")
    sandbox_config_raw = metadata.get("sandbox_config")
    patched_sha256 = metadata.get("patched_sha256")
    proxy_base = metadata.get("proxy_base")
    upstream_base = metadata.get("upstream_base")
    if not all(isinstance(value, str) and value for value in (source_config_raw, sandbox_config_raw, patched_sha256)):
        return
    sandbox_config = Path(sandbox_config_raw)
    if not sandbox_config.is_file():
        return
    try:
        final_text = sandbox_config.read_text(encoding="utf-8")
    except OSError:
        return
    if _sha256_text(final_text) == patched_sha256:
        return
    if isinstance(proxy_base, str) and isinstance(upstream_base, str) and proxy_base and upstream_base:
        final_text = final_text.replace(proxy_base, upstream_base)
    source_config = Path(source_config_raw)
    source_config.parent.mkdir(parents=True, exist_ok=True)
    source_config.write_text(final_text, encoding="utf-8")


_KIMI_CODE_SANDBOX_LINKS: tuple[tuple[str, bool], ...] = (
    ("oauth", True),
    ("credentials", True),
    ("plugins", True),
    ("skills", True),
    ("sessions", True),
    ("AGENTS.md", False),
    ("mcp.json", False),
    ("tui.toml", False),
)
_KIMI_CODE_CONFIG_METADATA = ".claude-tap-config-metadata.json"


def _link_kimi_code_sandbox_path(source_home: Path, sandbox: Path, rel: str, *, is_dir: bool) -> None:
    source = source_home / rel
    target = sandbox / rel
    if rel in ("oauth", "credentials") and not source.exists():
        source.mkdir(parents=True, exist_ok=True)
    if not source.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        if is_dir:
            target.symlink_to(source, target_is_directory=True)
        else:
            target.symlink_to(source)
    except OSError:
        if is_dir:
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            shutil.copy2(source, target)


def _persist_kimi_code_sandbox(source_home: Path, sandbox: Path) -> None:
    """Copy sandbox-only auth/session files back when symlinks were unavailable."""
    _persist_kimi_code_config_edits(sandbox)
    for name, is_dir in _KIMI_CODE_SANDBOX_LINKS:
        path = sandbox / name
        if not path.exists() or path.is_symlink():
            continue
        dest = source_home / name
        if is_dir:
            if dest.exists():
                if dest.is_dir() and not dest.is_symlink():
                    shutil.rmtree(dest)
                else:
                    dest.unlink()
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(path, dest)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)


_KIMI_CODE_SESSION_TEXT_SUFFIXES = frozenset({".json", ".jsonl", ".md", ".log", ".txt"})


def _normalize_kimi_code_fs_path(path: str) -> str:
    """Keep macOS temp paths on /var so they match KIMI_CODE_HOME join() results."""
    resolved = str(Path(path).expanduser().resolve())
    if resolved.startswith("/private/var/"):
        return "/var" + resolved[len("/private/var") :]
    return resolved


def _translate_kimi_code_home_path(path: str, old_prefix: str, new_prefix: str) -> str:
    if not path:
        return path
    resolved = _normalize_kimi_code_fs_path(path)
    old = _normalize_kimi_code_fs_path(old_prefix).rstrip("/")
    new = _normalize_kimi_code_fs_path(new_prefix).rstrip("/")
    if resolved == old:
        return new
    if resolved.startswith(old + "/"):
        return new + resolved[len(old) :]
    return path


def _iter_kimi_code_session_index_entries(path: Path) -> Iterable[dict[str, object]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            entry = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(entry, dict):
            yield entry


def _materialize_kimi_code_session_index(source_home: Path, sandbox: Path) -> None:
    """Copy session_index into the sandbox with sessionDir paths under KIMI_CODE_HOME."""
    source_index = source_home / "session_index.jsonl"
    target_index = sandbox / "session_index.jsonl"
    source_prefix = _normalize_kimi_code_fs_path(str(source_home))
    sandbox_prefix = _normalize_kimi_code_fs_path(str(sandbox))
    lines_out: list[str] = []
    if source_index.is_file():
        for entry in _iter_kimi_code_session_index_entries(source_index):
            session_dir = entry.get("sessionDir")
            if isinstance(session_dir, str):
                entry["sessionDir"] = _translate_kimi_code_home_path(session_dir, source_prefix, sandbox_prefix)
            lines_out.append(json.dumps(entry, ensure_ascii=False))
    target_index.parent.mkdir(parents=True, exist_ok=True)
    if lines_out:
        target_index.write_text("\n".join(lines_out) + "\n", encoding="utf-8")
    else:
        target_index.write_text("", encoding="utf-8")


def _merge_kimi_code_session_index(source_home: Path, sandbox: Path) -> None:
    """Merge sandbox session_index updates back into the real home."""
    sandbox_index = sandbox / "session_index.jsonl"
    if not sandbox_index.is_file():
        return
    source_index = source_home / "session_index.jsonl"
    source_prefix = _normalize_kimi_code_fs_path(str(source_home))
    sandbox_prefix = _normalize_kimi_code_fs_path(str(sandbox))
    entries: dict[str, dict[str, object]] = {}

    def ingest_index(path: Path, *, from_sandbox: bool) -> None:
        for entry in _iter_kimi_code_session_index_entries(path):
            session_id = entry.get("sessionId")
            if not isinstance(session_id, str) or not session_id:
                continue
            session_dir = entry.get("sessionDir")
            if isinstance(session_dir, str):
                entry["sessionDir"] = (
                    _translate_kimi_code_home_path(session_dir, sandbox_prefix, source_prefix)
                    if from_sandbox
                    else _normalize_kimi_code_fs_path(session_dir)
                )
            entries[session_id] = entry

    if source_index.is_file():
        ingest_index(source_index, from_sandbox=False)
    ingest_index(sandbox_index, from_sandbox=True)

    source_index.parent.mkdir(parents=True, exist_ok=True)
    merged = "\n".join(json.dumps(entries[session_id], ensure_ascii=False) for session_id in entries) + "\n"
    source_index.write_text(merged, encoding="utf-8")


def _kimi_code_path_prefix_variants(prefix: str) -> tuple[str, ...]:
    normalized = _normalize_kimi_code_fs_path(prefix)
    variants = [normalized]
    if normalized.startswith("/var/"):
        private_variant = "/private" + normalized
        if private_variant not in variants:
            variants.append(private_variant)
    if prefix not in variants and prefix != normalized:
        variants.append(prefix)
    return tuple(sorted(variants, key=len, reverse=True))


def _rewrite_kimi_code_text_paths(text: str, sandbox_prefix: str, source_prefix: str) -> str:
    target_prefix = _normalize_kimi_code_fs_path(source_prefix)
    rewritten = text
    for old_prefix in _kimi_code_path_prefix_variants(sandbox_prefix):
        rewritten = rewritten.replace(old_prefix, target_prefix)
    return rewritten


def _remap_kimi_code_sandbox_paths(source_home: Path, sandbox: Path) -> None:
    """Rewrite kimi-code session metadata that still points at the temp sandbox."""
    sandbox_prefix = _normalize_kimi_code_fs_path(str(sandbox))
    source_prefix = _normalize_kimi_code_fs_path(str(source_home))
    if sandbox_prefix == source_prefix:
        return

    index_path = source_home / "session_index.jsonl"
    if index_path.is_file():
        index_text = index_path.read_text(encoding="utf-8")
        rewritten = _rewrite_kimi_code_text_paths(index_text, sandbox_prefix, source_prefix)
        if rewritten != index_text:
            index_path.write_text(rewritten, encoding="utf-8")

    sessions_root = source_home / "sessions"
    if not sessions_root.is_dir():
        return
    for path in sessions_root.rglob("*"):
        if not path.is_file() or path.suffix not in _KIMI_CODE_SESSION_TEXT_SUFFIXES:
            continue
        try:
            original = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rewritten = _rewrite_kimi_code_text_paths(original, sandbox_prefix, source_prefix)
        if rewritten != original:
            path.write_text(rewritten, encoding="utf-8")


def _kimi_code_config_model_target(config: dict[str, object], model_name: str) -> str | None:
    models = config.get("models")
    providers = config.get("providers")
    if not model_name.strip() or not isinstance(models, dict) or not isinstance(providers, dict):
        return None
    alias = models.get(model_name.strip())
    if not isinstance(alias, dict):
        return None
    provider_name = alias.get("provider")
    if not isinstance(provider_name, str):
        return None
    provider = providers.get(provider_name)
    if not isinstance(provider, dict):
        return None
    base_url = _kimi_code_provider_base_url(provider)
    if base_url:
        return base_url
    if provider.get("type") == "kimi":
        return CLIENT_CONFIGS["kimi-code"].default_target
    return None


def _prepare_kimi_code_reverse_sandbox(
    port: int, cmd_args: Sequence[str] = ()
) -> tuple[Path, list[str], Path, list[str]]:
    source_home = _kimi_code_source_home()
    proxy_base = f"http://127.0.0.1:{port}"
    upstream_base = _detect_kimi_code_target(cmd_args)
    sandbox = Path(tempfile.mkdtemp(prefix=_KIMI_CODE_SANDBOX_DIR_PREFIX))
    patched_cmd_args = list(cmd_args)
    inline_config = _kimi_code_inline_config_arg(cmd_args)
    config_file_arg = _kimi_code_config_file_arg(cmd_args)

    if inline_config:
        patched_inline = _patch_kimi_code_inline_config(inline_config, proxy_base, cmd_args)
        patched_cmd_args = _replace_kimi_code_option_value(cmd_args, {"--config"}, patched_inline)
        (sandbox / "config.toml").write_text(_minimal_kimi_code_config_toml(proxy_base), encoding="utf-8")
        patched_providers = [_KIMI_CODE_MANAGED_PROVIDER]
    elif config_file_arg:
        source_config = Path(config_file_arg).expanduser()
        target_config = sandbox / ("config.json" if source_config.suffix.lower() == ".json" else "config.toml")
        try:
            source_text = source_config.read_text(encoding="utf-8")
        except OSError:
            source_text = ""
        if target_config.suffix.lower() == ".json" and source_text.strip():
            config = _read_kimi_code_config(path=source_config)
            patched, patched_providers = _patch_kimi_code_config_dict(config, proxy_base, cmd_args)
            target_config.write_text(json.dumps(patched, indent=2) + "\n", encoding="utf-8")
        else:
            patched_text, patched_providers = _patch_kimi_code_config_text(source_text, proxy_base, cmd_args)
            if not patched_providers:
                if _kimi_code_config_has_launch_state(source_text):
                    patched_text = source_text
                else:
                    patched_text = _minimal_kimi_code_config_toml(proxy_base)
                    patched_providers = [_KIMI_CODE_MANAGED_PROVIDER]
            target_config.write_text(patched_text, encoding="utf-8")
        _write_kimi_code_config_metadata(
            sandbox,
            source_config=source_config,
            sandbox_config=target_config,
            patched_text=target_config.read_text(encoding="utf-8"),
            proxy_base=proxy_base,
            upstream_base=upstream_base,
        )
        patched_cmd_args = _replace_kimi_code_option_value(cmd_args, {"--config-file"}, str(target_config))
    else:
        source_config = source_home / "config.toml"
        target_config = sandbox / "config.toml"
        if source_config.is_file():
            source_text = source_config.read_text(encoding="utf-8")
            patched_text, patched_providers = _patch_kimi_code_config_text(source_text, proxy_base, cmd_args)
            if not patched_providers:
                if _kimi_code_config_has_launch_state(source_text):
                    patched_text = source_text
                else:
                    patched_text = _minimal_kimi_code_config_toml(proxy_base)
                    patched_providers = [_KIMI_CODE_MANAGED_PROVIDER]
            target_config.write_text(patched_text, encoding="utf-8")
        else:
            target_config.write_text(_minimal_kimi_code_config_toml(proxy_base), encoding="utf-8")
            patched_providers = [_KIMI_CODE_MANAGED_PROVIDER]
        _write_kimi_code_config_metadata(
            sandbox,
            source_config=source_config,
            sandbox_config=target_config,
            patched_text=target_config.read_text(encoding="utf-8"),
            proxy_base=proxy_base,
            upstream_base=upstream_base,
        )

    for rel, is_dir in _KIMI_CODE_SANDBOX_LINKS:
        _link_kimi_code_sandbox_path(source_home, sandbox, rel, is_dir=is_dir)

    _materialize_kimi_code_session_index(source_home, sandbox)

    _sync_kimi_code_migration_suppression(source_home, sandbox)

    return sandbox, patched_providers, source_home, patched_cmd_args


def _detect_kimi_code_target(cmd_args: Sequence[str] = ()) -> str:
    config = _kimi_code_config_for_args(cmd_args)
    model_arg = _kimi_code_model_arg(cmd_args)
    if model_arg:
        base_url = _kimi_code_config_model_target(config, model_arg)
        if base_url:
            return base_url

    env_keys = ["KIMI_BASE_URL", "KIMI_CODE_BASE_URL"]
    if _has_active_kimi_code_model_env():
        env_keys.insert(0, "KIMI_MODEL_BASE_URL")
    for env_key in env_keys:
        base_url = os.environ.get(env_key, "").strip()
        if base_url:
            return base_url

    selected_model = model_arg
    if not selected_model:
        default_model = config.get("default_model")
        if isinstance(default_model, str) and default_model.strip():
            selected_model = default_model.strip()
    if isinstance(selected_model, str) and selected_model.strip():
        base_url = _kimi_code_config_model_target(config, selected_model.strip())
        if base_url:
            return base_url

    providers = config.get("providers")
    if isinstance(providers, dict):
        managed = providers.get(_KIMI_CODE_MANAGED_PROVIDER)
        if isinstance(managed, dict):
            base_url = _kimi_code_provider_base_url(managed)
            if base_url:
                return base_url
        for provider in providers.values():
            if isinstance(provider, dict) and provider.get("type") == "kimi":
                base_url = _kimi_code_provider_base_url(provider)
                if base_url:
                    return base_url

    return CLIENT_CONFIGS["kimi-code"].default_target


_OPENCLAW_CLEANUP_ENV = "__CLAUDE_TAP_OPENCLAW_CONFIG__"


def _read_openclaw_config(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _openclaw_config_path() -> Path:
    explicit = os.environ.get("OPENCLAW_CONFIG_PATH", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    state_dir = os.environ.get("OPENCLAW_STATE_DIR", "").strip()
    if state_dir:
        return Path(state_dir).expanduser() / "openclaw.json"
    return Path.home() / ".openclaw" / "openclaw.json"


def _openclaw_model_arg(cmd_args: Sequence[str]) -> str | None:
    for idx, arg in enumerate(cmd_args):
        if arg in {"--model", "-m"} and idx + 1 < len(cmd_args):
            value = cmd_args[idx + 1].strip()
            if value:
                return value
        if arg.startswith("--model="):
            value = arg.split("=", 1)[1].strip()
            if value:
                return value
    return None


def _openclaw_primary_model(cfg: dict, cmd_args: Sequence[str] = ()) -> str | None:
    if model_arg := _openclaw_model_arg(cmd_args):
        return model_arg
    agents = cfg.get("agents")
    if not isinstance(agents, dict):
        return None
    defaults = agents.get("defaults")
    if not isinstance(defaults, dict):
        return None
    model = defaults.get("model")
    if isinstance(model, str):
        return model
    if isinstance(model, dict):
        primary = model.get("primary")
        if isinstance(primary, str):
            return primary
    models = defaults.get("models")
    if isinstance(models, dict):
        for key in models:
            if isinstance(key, str):
                return key
    return None


def _openclaw_provider_proxy_url(provider: dict, proxy_url: str) -> str:
    api = provider.get("api")
    if not isinstance(api, str):
        return f"{proxy_url}/v1"
    if api.startswith("openai-"):
        return f"{proxy_url}/v1"
    return proxy_url


def _openclaw_provider_target_url(provider: dict, base_url: str) -> str:
    target = base_url.strip().rstrip("/")
    if _openclaw_provider_proxy_url(provider, "http://127.0.0.1:0").endswith("/v1") and target.endswith("/v1"):
        return target[:-3].rstrip("/") or target
    return target


def _openclaw_config_with_proxy(cfg: dict, proxy_url: str, cmd_args: Sequence[str] = ()) -> dict | None:
    model = _openclaw_primary_model(cfg, cmd_args)
    if not model or "/" not in model:
        return None
    provider_id = model.split("/", 1)[0]
    models = cfg.get("models")
    if not isinstance(models, dict):
        return None
    providers = models.get("providers")
    if not isinstance(providers, dict):
        return None
    provider = providers.get(provider_id)
    if not isinstance(provider, dict):
        return None
    patched = json.loads(json.dumps(cfg))
    patched_provider = patched["models"]["providers"][provider_id]
    patched_provider["baseUrl"] = _openclaw_provider_proxy_url(provider, proxy_url)
    patched_provider.pop("base_url", None)
    return patched


def _openclaw_reverse_env(port: int, cmd_args: Sequence[str] = ()) -> dict[str, str]:
    proxy_url = f"http://127.0.0.1:{port}"
    cfg = _read_openclaw_config(_openclaw_config_path())
    if cfg:
        patched = _openclaw_config_with_proxy(cfg, proxy_url, cmd_args)
        if patched:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".openclaw.json", delete=False) as f:
                json.dump(patched, f, indent=2)
                f.write("\n")
                tmp_path = f.name
            return {"OPENCLAW_CONFIG_PATH": tmp_path, _OPENCLAW_CLEANUP_ENV: tmp_path}
    return _openclaw_fallback_reverse_env(proxy_url, cmd_args)


def _openclaw_fallback_reverse_env(proxy_url: str, cmd_args: Sequence[str] = ()) -> dict[str, str]:
    provider = _openclaw_fallback_provider(cmd_args)
    if provider == "anthropic":
        return {"ANTHROPIC_BASE_URL": proxy_url}
    if provider in {"gemini", "google"}:
        return {"GOOGLE_GEMINI_BASE_URL": proxy_url}
    if provider == "openrouter":
        return {"OPENROUTER_BASE_URL": proxy_url}
    return {"OPENAI_BASE_URL": f"{proxy_url}/v1"}


def _openclaw_fallback_provider(cmd_args: Sequence[str] = ()) -> str:
    model = _openclaw_primary_model({}, cmd_args)
    if model and "/" in model:
        return model.split("/", 1)[0]
    for env_key, provider in (
        ("OPENAI_API_KEY", "openai"),
        ("ANTHROPIC_API_KEY", "anthropic"),
        ("GEMINI_API_KEY", "gemini"),
        ("GOOGLE_API_KEY", "gemini"),
        ("OPENROUTER_API_KEY", "openrouter"),
    ):
        if os.environ.get(env_key):
            return provider
    return "openai"


def _opencode_reverse_env(port: int) -> dict[str, str]:
    proxy_url = f"http://127.0.0.1:{port}"
    return {
        "ANTHROPIC_BASE_URL": proxy_url,
        "OPENAI_BASE_URL": f"{proxy_url}/v1",
        "GOOGLE_GEMINI_BASE_URL": proxy_url,
    }


def _multi_provider_reverse_env(port: int) -> dict[str, str]:
    proxy_url = f"http://127.0.0.1:{port}"
    return {
        "KIMI_BASE_URL": proxy_url,
        "MOONSHOT_BASE_URL": f"{proxy_url}/v1",
        "OPENAI_BASE_URL": f"{proxy_url}/v1",
        "ANTHROPIC_BASE_URL": proxy_url,
        "GOOGLE_GEMINI_BASE_URL": proxy_url,
        "OPENROUTER_BASE_URL": f"{proxy_url}/v1",
        "CUSTOM_BASE_URL": f"{proxy_url}/v1",
    }


def _detect_openclaw_target(cmd_args: Sequence[str] = ()) -> str:
    cfg = _read_openclaw_config(_openclaw_config_path())
    if cfg:
        model = _openclaw_primary_model(cfg, cmd_args)
        if model and "/" in model:
            provider_id = model.split("/", 1)[0]
            models = cfg.get("models")
            providers = models.get("providers") if isinstance(models, dict) else None
            provider = providers.get(provider_id) if isinstance(providers, dict) else None
            if isinstance(provider, dict):
                base = provider.get("baseUrl") or provider.get("base_url")
                if isinstance(base, str) and base.strip():
                    return _openclaw_provider_target_url(provider, base)
    for env_key, target in (
        ("OPENAI_API_KEY", "https://api.openai.com"),
        ("ANTHROPIC_API_KEY", "https://api.anthropic.com"),
        ("GEMINI_API_KEY", "https://generativelanguage.googleapis.com"),
        ("GOOGLE_API_KEY", "https://generativelanguage.googleapis.com"),
        ("OPENROUTER_API_KEY", "https://openrouter.ai/api/v1"),
    ):
        if os.environ.get(env_key):
            return target
    return CLIENT_CONFIGS["openclaw"].default_target


TARGET_DETECTORS = {
    "claude": _detect_claude_target,
    "codex": _detect_codex_target,
    "codebuddy": _detect_codebuddy_target,
    "grok": _detect_grok_target,
    "kimi-code": _detect_kimi_code_target,
    "openclaw": _detect_openclaw_target,
}
