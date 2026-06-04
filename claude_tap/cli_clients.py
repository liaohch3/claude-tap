"""Client launch and target detection helpers for claude-tap CLI."""

from __future__ import annotations

import asyncio
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
from urllib.parse import urlparse

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


def _should_rewrite_extra_base_url_env(env_key: str) -> bool:
    if env_key != "ANTHROPIC_BEDROCK_BASE_URL":
        return True
    if not _is_claude_bedrock_enabled():
        return False
    current_value = _resolve_env_value(env_key)
    if not current_value:
        return False
    return not (current_value and _is_aws_native_bedrock_url(current_value))


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
        extra_base_url_envs=("ANTHROPIC_BEDROCK_BASE_URL",),
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
    client_cmd: str | None = None,
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

    cmd_args = list(extra_args)
    cmd_args = _maybe_rewrite_hermes_gateway_start(client, cmd_args)
    has_base_url_config_override = bool(
        cfg.base_url_config_key and _has_config_override(cmd_args, cfg.base_url_config_key)
    )

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
            kimi_code_sandbox, _patched_providers, kimi_code_source_home = _prepare_kimi_code_reverse_sandbox(port)
            env["KIMI_CODE_HOME"] = str(kimi_code_sandbox)
            env["KIMI_CODE_BASE_URL"] = cfg.reverse_base_url(port)
            env["NO_PROXY"] = "127.0.0.1"
        else:
            reverse_env = cfg.reverse_base_url_env_map(port)
            env.update(reverse_env)
            env["NO_PROXY"] = "127.0.0.1"
        if cfg.inject_settings_env and not _has_settings_arg(cmd_args):
            cmd_args = _settings_arg(reverse_env) + cmd_args
        base_url_config_overrides: list[str] = []
        if cfg.base_url_config_key and not has_base_url_config_override:
            # Some clients ignore their base URL env in selected auth/transport modes
            # unless the same value is also supplied as a config override.
            base_url = cfg.reverse_base_url(port)
            base_url_config_overrides.append(f'{cfg.base_url_config_key}="{base_url}"')
        if client == "codex":
            provider_base_url_key = _codex_selected_provider_base_url_key(cmd_args)
            if provider_base_url_key and not _has_config_override(cmd_args, provider_base_url_key):
                # Codex custom providers ignore the legacy openai_base_url key.
                # Override the selected provider directly so reverse mode captures
                # New API and other OpenAI-compatible gateways.
                base_url = cfg.reverse_base_url(port)
                base_url_config_overrides.append(f'{provider_base_url_key}="{base_url}"')
        if base_url_config_overrides:
            injected: list[str] = []
            for override in base_url_config_overrides:
                injected.extend(["-c", override])
            cmd_args = injected + cmd_args

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

    try:
        code = await proc.wait()
    finally:
        if (
            client == "kimi-code"
            and proxy_mode == "reverse"
            and kimi_code_sandbox is not None
            and kimi_code_source_home is not None
        ):
            _persist_kimi_code_sandbox(kimi_code_source_home, kimi_code_sandbox)
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


def _read_codex_config() -> dict[str, object]:
    config_path = _codex_home() / "config.toml"
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


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

    profiles = data.get("profiles")
    if profile and isinstance(profiles, dict):
        profile_config = profiles.get(profile)
        if isinstance(profile_config, dict) and not isinstance(provider, str):
            provider = profile_config.get("model_provider")

    if not isinstance(provider, str):
        provider = data.get("model_provider")
    if not isinstance(provider, str) or not provider.strip():
        return None

    providers = data.get("model_providers")
    if not isinstance(providers, dict):
        return None
    provider_config = providers.get(provider)
    if not isinstance(provider_config, dict):
        return None
    base_url = provider_config.get("base_url")
    if not isinstance(base_url, str) or not base_url.strip():
        return None
    return provider.strip(), base_url.strip()


def _codex_selected_provider_base_url_key(args: list[str] | None = None) -> str | None:
    selected = _selected_codex_provider_base_url(args)
    if selected is None:
        return None
    provider, _base_url = selected
    return f"model_providers.{_toml_dotted_key_segment(provider)}.base_url"


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

    Claude Code can source ``ANTHROPIC_BASE_URL`` from settings files rather
    than the process environment. When Bedrock mode is enabled, it can also
    source ``ANTHROPIC_BEDROCK_BASE_URL``. Mirror that behavior for custom
    gateways without forcing users to repeat ``--tap-target``.
    """
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

    Reads ``~/.codex/auth.json`` (or ``$CODEX_HOME/auth.json``) to determine
    the auth mode.  ChatGPT OAuth users (``codex login``) need the chatgpt.com
    backend; API-key users use api.openai.com unless their Codex config selects
    a custom provider with its own base URL.
    """
    codex_home = _codex_home()
    auth_file = codex_home / "auth.json"
    try:
        data = json.loads(auth_file.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("auth_mode") == "chatgpt":
            return _CODEX_CHATGPT_TARGET
    except (OSError, json.JSONDecodeError, ValueError):
        pass

    custom_provider = _selected_codex_provider_base_url(args)
    if custom_provider is not None:
        _provider, base_url = custom_provider
        return base_url

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
_KIMI_CODE_PROXY_HOST_MARKERS = ("kimi.com", "moonshot.ai", "moonshot.cn")
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


def _read_kimi_code_config(home: Path | None = None) -> dict[str, object]:
    config_path = (home or _kimi_code_home()) / "config.toml"
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


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


def _should_proxy_kimi_code_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(marker in host for marker in _KIMI_CODE_PROXY_HOST_MARKERS)


def _remap_kimi_code_service_base_url(original: str, proxy_base: str) -> str:
    proxy_base = proxy_base.rstrip("/")
    original = original.rstrip("/")
    for prefix in (
        "https://api.kimi.com/coding/v1",
        "https://api.moonshot.ai/v1",
        "https://api.moonshot.cn/v1",
    ):
        if original.startswith(prefix):
            suffix = original[len(prefix) :]
            return f"{proxy_base}{suffix}"
    return proxy_base


def _collect_kimi_code_provider_urls(config: dict[str, object]) -> list[str]:
    urls: list[str] = []
    providers = config.get("providers")
    if not isinstance(providers, dict):
        return urls
    for provider in providers.values():
        if not isinstance(provider, dict) or provider.get("type") != "kimi":
            continue
        base_url = _kimi_code_provider_base_url(provider)
        if base_url:
            urls.append(base_url)
    return urls


def _collect_kimi_code_service_urls(config: dict[str, object]) -> list[str]:
    urls: list[str] = []
    services = config.get("services")
    if not isinstance(services, dict):
        return urls
    for service in services.values():
        if not isinstance(service, dict):
            continue
        base_url = service.get("base_url")
        if isinstance(base_url, str) and base_url.strip() and _should_proxy_kimi_code_url(base_url):
            urls.append(base_url.strip())
    return urls


def _collect_kimi_code_base_urls(config: dict[str, object]) -> list[str]:
    return _collect_kimi_code_provider_urls(config) + _collect_kimi_code_service_urls(config)


def _patch_kimi_code_config_dict(config: dict[str, object], proxy_base: str) -> tuple[dict[str, object], list[str]]:
    patched = json.loads(json.dumps(config))
    patched_providers: list[str] = []

    providers = patched.get("providers")
    if isinstance(providers, dict):
        for name, provider in providers.items():
            if not isinstance(provider, dict) or provider.get("type") != "kimi":
                continue
            provider["base_url"] = proxy_base
            env_table = provider.get("env")
            if isinstance(env_table, dict) and "KIMI_BASE_URL" in env_table:
                env_table["KIMI_BASE_URL"] = proxy_base
            patched_providers.append(str(name))

    services = patched.get("services")
    if isinstance(services, dict):
        for service in services.values():
            if not isinstance(service, dict):
                continue
            base_url = service.get("base_url")
            if isinstance(base_url, str) and base_url.strip() and _should_proxy_kimi_code_url(base_url):
                service["base_url"] = _remap_kimi_code_service_base_url(base_url, proxy_base)

    return patched, patched_providers


def _proxy_base_url_for_original(old_url: str, proxy_base: str) -> str:
    if "/search" in old_url or "/fetch" in old_url:
        return _remap_kimi_code_service_base_url(old_url, proxy_base)
    return proxy_base


def _kimi_code_config_url_replacements(config: dict[str, object], proxy_base: str) -> list[tuple[str, str]]:
    replacements: list[tuple[str, str]] = []
    for old_url in _collect_kimi_code_provider_urls(config):
        replacements.append((old_url, proxy_base))
    for old_url in _collect_kimi_code_service_urls(config):
        replacements.append((old_url, _remap_kimi_code_service_base_url(old_url, proxy_base)))
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
    pattern = rf'(?m)^((?:base_url|KIMI_BASE_URL)\s*=\s*["\']){escaped}(["\']\s*)$'
    return re.sub(pattern, rf"\1{new_url}\2", text)


def _patch_kimi_code_config_text(source_text: str, proxy_base: str) -> tuple[str, list[str]]:
    if not source_text.strip():
        return _minimal_kimi_code_config_toml(proxy_base), [_KIMI_CODE_MANAGED_PROVIDER]
    try:
        config = tomllib.loads(source_text)
    except (tomllib.TOMLDecodeError, ValueError):
        config = {}
    if not isinstance(config, dict):
        config = {}
    _, patched_providers = _patch_kimi_code_config_dict(config, proxy_base)
    result = source_text
    for old_url, new_url in _kimi_code_config_url_replacements(config, proxy_base):
        result = _replace_kimi_code_toml_url_assignments(result, old_url, new_url)
    return result, patched_providers


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


_KIMI_CODE_SANDBOX_LINKS: tuple[tuple[str, bool], ...] = (
    ("oauth", True),
    ("credentials", True),
    ("sessions", True),
    ("mcp.json", False),
    ("session_index.jsonl", False),
)


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
    for name, is_dir in _KIMI_CODE_SANDBOX_LINKS:
        path = sandbox / name
        if not path.exists() or path.is_symlink():
            continue
        dest = source_home / name
        if is_dir:
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copytree(path, dest, dirs_exist_ok=True)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)


def _prepare_kimi_code_reverse_sandbox(port: int) -> tuple[Path, list[str], Path]:
    source_home = _kimi_code_source_home()
    proxy_base = f"http://127.0.0.1:{port}"
    sandbox = Path(tempfile.mkdtemp(prefix=_KIMI_CODE_SANDBOX_DIR_PREFIX))
    source_config = source_home / "config.toml"
    target_config = sandbox / "config.toml"

    if source_config.is_file():
        source_text = source_config.read_text(encoding="utf-8")
        patched_text, patched_providers = _patch_kimi_code_config_text(source_text, proxy_base)
        target_config.write_text(patched_text, encoding="utf-8")
        if not patched_providers:
            patched_providers = [_KIMI_CODE_MANAGED_PROVIDER]
    else:
        target_config.write_text(_minimal_kimi_code_config_toml(proxy_base), encoding="utf-8")
        patched_providers = [_KIMI_CODE_MANAGED_PROVIDER]

    for rel, is_dir in _KIMI_CODE_SANDBOX_LINKS:
        _link_kimi_code_sandbox_path(source_home, sandbox, rel, is_dir=is_dir)

    _sync_kimi_code_migration_suppression(source_home, sandbox)

    return sandbox, patched_providers, source_home


def _detect_kimi_code_target() -> str:
    config = _read_kimi_code_config()
    default_model = config.get("default_model")
    models = config.get("models")
    providers = config.get("providers")
    if (
        isinstance(default_model, str)
        and default_model.strip()
        and isinstance(models, dict)
        and isinstance(providers, dict)
    ):
        alias = models.get(default_model.strip())
        if isinstance(alias, dict):
            provider_name = alias.get("provider")
            if isinstance(provider_name, str):
                provider = providers.get(provider_name)
                if isinstance(provider, dict):
                    base_url = _kimi_code_provider_base_url(provider)
                    if base_url:
                        return base_url

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


TARGET_DETECTORS = {
    "claude": _detect_claude_target,
    "codex": _detect_codex_target,
    "codebuddy": _detect_codebuddy_target,
    "kimi-code": _detect_kimi_code_target,
}
