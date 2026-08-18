from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from claude_tap import parse_args
from claude_tap.cli import CLIENT_CONFIGS, run_client
from claude_tap.forward_proxy import ForwardProxyServer


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


def test_mcode_registered_as_filtered_forward_proxy_client() -> None:
    cfg = CLIENT_CONFIGS["mcode"]

    assert cfg.cmd == "mcode"
    assert cfg.label == "MiniMax Code"
    assert cfg.install_url == "https://www.npmjs.com/package/@minimax-ai/code"
    assert cfg.base_url_env == ""
    assert cfg.default_proxy_mode == "forward"
    assert cfg.forward_trace_methods == ("POST", "WEBSOCKET")
    assert cfg.forward_trace_path_prefixes == ("/backend-api/codex/responses",)
    assert cfg.forward_trace_model_requests_only is True
    assert cfg.forward_trace_path_suffixes == (
        "/messages",
        "/messages/count_tokens",
        "/chat/completions",
        "/responses",
    )


def test_parse_args_mcode_defaults_to_forward_mode() -> None:
    args = parse_args(["--tap-client", "mcode"])

    assert args.client == "mcode"
    assert args.proxy_mode == "forward"


def test_parse_args_mcode_rejects_reverse_mode(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        parse_args(["--tap-client", "mcode", "--tap-proxy-mode", "reverse"])

    assert "only supports forward proxy mode" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("method", "path", "request_body", "expected"),
    [
        ("POST", "/mavis/api/v1/llm/v1/messages", {"model": "MiniMax-M3", "messages": []}, True),
        ("POST", "/v1/messages/count_tokens", {"model": "MiniMax-M3", "messages": []}, True),
        ("POST", "/v1/chat/completions", {"model": "custom-model", "messages": []}, True),
        ("POST", "/v1/responses", {"model": "gpt-5", "input": "hello"}, True),
        ("WEBSOCKET", "/backend-api/codex/responses", None, True),
        ("GET", "/v1/messages", None, False),
        ("POST", "/mavis/api/v1/auth/refresh", {"refresh_token": "secret"}, False),
        ("POST", "/v1/models", {}, False),
        ("POST", "/tools/messages", {"tool": "remote-service", "payload": "sensitive"}, False),
        ("POST", "/chat/completions", {"messages": []}, False),
        ("WEBSOCKET", "/tools/responses", None, False),
    ],
)
def test_mcode_capture_filter(method: str, path: str, request_body: dict | None, expected: bool) -> None:
    cfg = CLIENT_CONFIGS["mcode"]
    server = ForwardProxyServer(
        host="127.0.0.1",
        port=0,
        ca=object(),
        writer=object(),
        session=object(),
        trace_methods=cfg.forward_trace_methods,
        trace_path_prefixes=cfg.forward_trace_path_prefixes,
        trace_path_suffixes=cfg.forward_trace_path_suffixes,
        trace_model_requests_only=cfg.forward_trace_model_requests_only,
    )

    assert server._should_trace_request(method, path, request_body) is expected


@pytest.mark.asyncio
async def test_run_client_mcode_forward_sets_node_proxy_ca_and_preserves_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    ca_path = Path("/tmp/test-ca.pem")

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.setenv("NO_PROXY", "corp.example")
    monkeypatch.setenv("no_proxy", "internal.example")
    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/mcode")
    monkeypatch.setattr("claude_tap.cli_clients._node_supports_env_proxy", lambda _: True)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(
        43123,
        ["exec", "Reply OK"],
        client="mcode",
        proxy_mode="forward",
        ca_cert_path=ca_path,
    )

    assert code == 0
    assert captured["cmd"] == ("/tmp/mcode", "exec", "Reply OK")
    env = captured["env"]
    assert env["HTTP_PROXY"] == "http://127.0.0.1:43123"
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:43123"
    assert env["NODE_USE_ENV_PROXY"] == "1"
    assert env["NODE_EXTRA_CA_CERTS"] == str(ca_path)
    assert env["SSL_CERT_FILE"] == str(ca_path)
    assert env["NO_PROXY"] == ""
    assert env["no_proxy"] == ""


@pytest.mark.asyncio
async def test_run_client_mcode_rejects_node_without_env_proxy_support(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/mcode")
    monkeypatch.setattr("claude_tap.cli_clients._node_supports_env_proxy", lambda _: False)

    code = await run_client(43123, [], client="mcode", proxy_mode="forward")

    assert code == 1
    output = capsys.readouterr().out
    assert "MiniMax Code forward capture requires a Node runtime" in output
    assert "node --use-env-proxy --version" in output
