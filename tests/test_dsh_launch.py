from __future__ import annotations

import asyncio

import pytest

from claude_tap import parse_args
from claude_tap.cli import CLIENT_CONFIGS, _reverse_proxy_trace_options, run_client
from claude_tap.cli_clients import _detect_dsh_target, _node_supports_env_proxy


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


def test_dsh_registered_in_client_configs() -> None:
    cfg = CLIENT_CONFIGS["dsh"]

    assert cfg.cmd == "dsh"
    assert cfg.label == "DeepSeek Harness"
    assert cfg.default_target == "https://api.deepseek.com"
    assert cfg.base_url_env == "DEEPSEEK_BASE_URL"
    assert cfg.base_url_suffix == ""
    assert cfg.default_proxy_mode == "forward"
    assert cfg.forward_trace_methods == ("POST",)
    assert cfg.forward_trace_path_suffixes == ("/chat/completions",)


def test_parse_args_dsh_defaults_to_forward_mode() -> None:
    args = parse_args(["--tap-client", "dsh"])

    assert args.client == "dsh"
    assert args.target == "https://api.deepseek.com"
    assert args.proxy_mode == "forward"


@pytest.mark.asyncio
async def test_run_client_dsh_forward_enables_node_proxy_and_preserves_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    monkeypatch.setenv("NO_PROXY", "localhost,corp.example")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/dsh")
    monkeypatch.setattr("claude_tap.cli_clients._node_supports_env_proxy", lambda _: True)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(
        43123,
        ["--profile", "headless", "Reply OK"],
        client="dsh",
        proxy_mode="forward",
    )

    assert code == 0
    assert captured["cmd"] == ("/tmp/dsh", "--profile", "headless", "Reply OK")
    env = captured["env"]
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:43123"
    assert env["NODE_USE_ENV_PROXY"] == "1"
    assert env["NO_PROXY"] == ""
    assert env["no_proxy"] == ""
    assert "DEEPSEEK_BASE_URL" not in env


@pytest.mark.asyncio
async def test_run_client_dsh_forward_rejects_node_without_env_proxy_support(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/dsh")
    monkeypatch.setattr("claude_tap.cli_clients._node_supports_env_proxy", lambda _: False)

    code = await run_client(43123, [], client="dsh", proxy_mode="forward")

    assert code == 1
    output = capsys.readouterr().err
    assert "requires a Node runtime with --use-env-proxy support" in output
    assert "--tap-proxy-mode reverse" in output


def test_node_supports_env_proxy_probes_node_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr("claude_tap.cli_clients.shutil.which", lambda cmd, path=None: "/opt/node")
    monkeypatch.setattr("claude_tap.cli_clients.subprocess.run", fake_run)

    assert _node_supports_env_proxy({"PATH": "/opt/bin", "NODE_OPTIONS": "--inspect"})
    assert captured["cmd"] == ["/opt/node", "--use-env-proxy", "--version"]
    assert "NODE_OPTIONS" not in captured["env"]


@pytest.mark.asyncio
async def test_run_client_dsh_reverse_sets_base_url_and_preserves_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/dsh")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(
        43123,
        ["--profile", "headless", "Reply OK"],
        client="dsh",
        proxy_mode="reverse",
    )

    assert code == 0
    assert captured["cmd"] == ("/tmp/dsh", "--profile", "headless", "Reply OK")
    assert captured["env"]["DEEPSEEK_BASE_URL"] == "http://127.0.0.1:43123"


def test_detect_dsh_target_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://gateway.example.test")

    assert _detect_dsh_target() == "https://gateway.example.test"


def test_detect_dsh_target_falls_back_to_public_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)

    assert _detect_dsh_target() == "https://api.deepseek.com"


def test_dsh_reverse_trace_options_do_not_strip_path_prefix() -> None:
    options = _reverse_proxy_trace_options("dsh", "https://api.deepseek.com")

    assert options == {"strip_path_prefix": "", "force_http": False}
