from __future__ import annotations

import asyncio

import pytest

from claude_tap import parse_args
from claude_tap.cli import CLIENT_CONFIGS, _reverse_proxy_trace_options, run_client


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


def test_kimi_registered_in_client_configs() -> None:
    cfg = CLIENT_CONFIGS["kimi"]
    assert cfg.cmd == "kimi"
    assert cfg.label == "Kimi Code CLI"
    assert cfg.default_target == "https://api.kimi.com/coding/v1"
    assert cfg.base_url_env == "KIMI_BASE_URL"
    assert cfg.base_url_suffix == ""
    assert cfg.default_proxy_mode == "reverse"


def test_parse_args_kimi_defaults_to_reverse_mode() -> None:
    args = parse_args(["--tap-client", "kimi"])
    assert args.client == "kimi"
    assert args.target == "https://api.kimi.com/coding/v1"
    assert args.proxy_mode == "reverse"


def test_parse_args_accepts_every_registered_client(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))

    for client in CLIENT_CONFIGS:
        args = parse_args(["--tap-client", client])
        assert args.client == client


@pytest.mark.asyncio
async def test_run_client_kimi_reverse_sets_kimi_base_url(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/kimi")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(43123, ["--thinking"], client="kimi", proxy_mode="reverse")

    assert code == 0
    assert captured["cmd"] == ("/tmp/kimi", "--thinking")
    assert captured["env"]["KIMI_BASE_URL"] == "http://127.0.0.1:43123"


def test_kimi_reverse_trace_options_do_not_strip_path_prefix() -> None:
    options = _reverse_proxy_trace_options("kimi", "https://api.kimi.com/coding/v1")

    assert options == {
        "strip_path_prefix": "",
        "force_http": False,
    }
