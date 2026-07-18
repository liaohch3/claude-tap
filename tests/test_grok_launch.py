from __future__ import annotations

import asyncio

import pytest

from claude_tap import parse_args
from claude_tap.cli import CLIENT_CONFIGS, _reverse_proxy_path_prefixes, _reverse_proxy_trace_options, run_client
from claude_tap.cli_clients import _detect_grok_target
from claude_tap.proxy import _is_allowed_path, _matches_path_prefixes


class _DummyProc:
    def __init__(self) -> None:
        self.pid = 12345
        self.returncode: int | None = None

    async def wait(self) -> int:
        self.returncode = 0
        return 0

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9


def test_grok_registered_in_client_configs() -> None:
    cfg = CLIENT_CONFIGS["grok"]

    assert cfg.cmd == "grok"
    assert cfg.label == "Grok Build CLI"
    assert cfg.default_target == "https://cli-chat-proxy.grok.com/v1"
    assert cfg.base_url_env == "GROK_CLI_CHAT_PROXY_BASE_URL"
    assert cfg.base_url_suffix == "/v1"
    assert cfg.default_proxy_mode == "reverse"
    assert cfg.reverse_allowed_path_prefixes == (
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
    )
    assert cfg.reverse_trace_path_prefixes == (
        "/v1/responses",
        "/v1/chat/completions",
        "/v1/storage",
        "/v1/traces",
    )


def test_parse_args_grok_defaults_to_reverse_mode() -> None:
    args = parse_args(["--tap-client", "grok"])

    assert args.client == "grok"
    assert args.target == "https://cli-chat-proxy.grok.com/v1"
    assert args.proxy_mode == "reverse"


@pytest.mark.asyncio
async def test_run_client_grok_reverse_sets_chat_proxy_base_url_and_preserves_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/grok")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(43123, ["-p", "Reply OK"], client="grok", proxy_mode="reverse")

    assert code == 0
    assert captured["cmd"] == ("/tmp/grok", "-p", "Reply OK")
    assert captured["env"]["GROK_CLI_CHAT_PROXY_BASE_URL"] == "http://127.0.0.1:43123/v1"


def test_grok_reverse_trace_options_strip_local_v1_prefix() -> None:
    options = _reverse_proxy_trace_options("grok", "https://cli-chat-proxy.grok.com/v1")

    assert options == {
        "strip_path_prefix": "/v1",
        "force_http": False,
    }


def test_grok_control_plane_paths_are_allowed_but_not_traced() -> None:
    cfg = CLIENT_CONFIGS["grok"]

    for path in (
        "/v1/user",
        "/v1/settings",
        "/v1/bundle/archive",
        "/v1/subagents/bundle",
        "/v1/feedback/config",
        "/v1/deployment/config",
        "/v1/mcp/tools/list",
        "/v1/sessions/session-id/signals",
        "/v1/billing",
    ):
        assert _is_allowed_path(path, cfg.reverse_allowed_path_prefixes)
        assert not _matches_path_prefixes(path, cfg.reverse_trace_path_prefixes)

    assert _matches_path_prefixes("/v1/responses", cfg.reverse_trace_path_prefixes)
    assert _matches_path_prefixes("/v1/storage/signed-upload-url", cfg.reverse_trace_path_prefixes)
    assert _matches_path_prefixes("/v1/traces", cfg.reverse_trace_path_prefixes)


def test_extra_allowed_path_keeps_default_reverse_clients_tracing_all_allowed_requests() -> None:
    allowed_prefixes, trace_prefixes = _reverse_proxy_path_prefixes("claude", ("/v1/auxiliary",))

    assert allowed_prefixes == ("/v1/auxiliary",)
    assert trace_prefixes == ()


def test_extra_allowed_path_is_traced_by_grok_path_filter() -> None:
    allowed_prefixes, trace_prefixes = _reverse_proxy_path_prefixes("grok", ("/v1/diagnostics",))

    assert "/v1/diagnostics" in allowed_prefixes
    assert _matches_path_prefixes("/v1/diagnostics/events", trace_prefixes)


def test_detect_grok_target_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROK_CLI_CHAT_PROXY_BASE_URL", "https://grok-gateway.example.com/v1")

    assert _detect_grok_target() == "https://grok-gateway.example.com/v1"


def test_detect_grok_target_falls_back_to_official_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROK_CLI_CHAT_PROXY_BASE_URL", raising=False)

    assert _detect_grok_target() == "https://cli-chat-proxy.grok.com/v1"
