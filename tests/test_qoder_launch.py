from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from claude_tap import parse_args
from claude_tap.cli import CLIENT_CONFIGS, run_client
from claude_tap.proxy import _build_record
from tests.schema_types import Map


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


def test_qoder_registered_in_client_configs() -> None:
    cfg = CLIENT_CONFIGS["qoder"]

    assert cfg.cmd == "qodercli"
    assert cfg.label == "Qoder CLI"
    assert cfg.default_target == "https://api2.qoder.sh"
    assert cfg.base_url_env == "QODER_BASE_URL"
    assert cfg.base_url_suffix == ""
    assert cfg.default_proxy_mode == "forward"


def test_parse_args_qoder_defaults_to_forward_mode() -> None:
    args = parse_args(["--tap-client", "qoder"])

    assert args.client == "qoder"
    assert args.target == "https://api2.qoder.sh"
    assert args.proxy_mode == "forward"


def test_parse_args_qoder_explicit_reverse_overrides_default() -> None:
    args = parse_args(["--tap-client", "qoder", "--tap-proxy-mode", "reverse"])

    assert args.client == "qoder"
    assert args.proxy_mode == "reverse"


def test_qoder_trace_headers_redact_request_identity_and_response_cookie() -> None:
    record = _build_record(
        req_id="req_qoder",
        turn=1,
        duration_ms=42,
        method="POST",
        path_qs="/api/agent/query",
        req_headers={
            "Cosy-Key": "cosy-request-signature-secret",
            "Cosy-MachineToken": "qoder-machine-token-secret",
            "Cosy-MachineId": "qoder-machine-id-secret",
            "Cosy-User": "qoder-user-secret",
            "Content-Type": "application/json",
        },
        req_body={"prompt": "hello"},
        status=200,
        resp_headers={
            "Set-Cookie": "acw_tc=qoder-response-cookie-secret; Path=/",
            "Content-Type": "application/json",
        },
        resp_body={"ok": True},
    )

    req_headers = record["request"]["headers"]
    assert req_headers["Cosy-Key"] == "***"
    assert "signature-secret" not in req_headers["Cosy-Key"]
    assert req_headers["Cosy-MachineToken"] == "***"
    assert "machine-token-secret" not in req_headers["Cosy-MachineToken"]
    assert req_headers["Cosy-MachineId"] == "***"
    assert "machine-id-secret" not in req_headers["Cosy-MachineId"]
    assert req_headers["Cosy-User"] == "***"
    assert "user-secret" not in req_headers["Cosy-User"]
    assert req_headers["Content-Type"] == "application/json"

    resp_headers = record["response"]["headers"]
    assert resp_headers["Set-Cookie"] == "***"
    assert "response-cookie-secret" not in resp_headers["Set-Cookie"]
    assert resp_headers["Content-Type"] == "application/json"


@pytest.mark.asyncio
async def test_run_client_qoder_forward_sets_proxy_ca_and_preserves_args(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: Map[str, object] = {}
    ca_path = Path("/tmp/test-ca.pem")

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.delenv("QODER_BASE_URL", raising=False)
    monkeypatch.setenv("NO_PROXY", "example.com")
    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/qodercli")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(
        43123,
        ["-p", "hello", "--permission-mode", "dont_ask"],
        client="qoder",
        proxy_mode="forward",
        ca_cert_path=ca_path,
    )

    assert code == 0
    assert captured["cmd"] == ("/tmp/qodercli", "-p", "hello", "--permission-mode", "dont_ask")
    env = captured["env"]
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:43123"
    assert env["http_proxy"] == "http://127.0.0.1:43123"
    assert env["NODE_EXTRA_CA_CERTS"] == str(ca_path)
    assert env["SSL_CERT_FILE"] == str(ca_path)
    assert "example.com" in env["NO_PROXY"]
    assert "localhost" in env["NO_PROXY"]
    assert "127.0.0.1" in env["NO_PROXY"]
    assert env["no_proxy"] == env["NO_PROXY"]
    assert "QODER_BASE_URL" not in env


@pytest.mark.asyncio
async def test_run_client_qoder_reverse_sets_structural_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: Map[str, object] = {}

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/qodercli")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(43123, ["-p", "hello"], client="qoder", proxy_mode="reverse")

    assert code == 0
    assert captured["cmd"] == ("/tmp/qodercli", "-p", "hello")
    assert captured["env"]["QODER_BASE_URL"] == "http://127.0.0.1:43123"
