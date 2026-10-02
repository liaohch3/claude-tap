from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path

import pytest

from claude_tap import parse_args
from claude_tap.cli import CLIENT_CONFIGS, run_client
from claude_tap.cli_clients import _rewrite_pi_models_payload


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


def test_pi_registered_in_client_configs() -> None:
    cfg = CLIENT_CONFIGS["pi"]

    assert cfg.cmd == "pi"
    assert cfg.label == "Pi"
    assert cfg.default_target == "https://api.openai.com"
    assert cfg.base_url_env == "OPENAI_BASE_URL"
    assert cfg.base_url_suffix == "/v1"
    assert cfg.default_proxy_mode == "forward"


def test_parse_args_pi_defaults_to_forward_mode() -> None:
    args = parse_args(["--tap-client", "pi"])

    assert args.client == "pi"
    assert args.target == "https://api.openai.com"
    assert args.proxy_mode == "forward"


def test_parse_args_pi_explicit_reverse_overrides_default() -> None:
    args = parse_args(["--tap-client", "pi", "--tap-proxy-mode", "reverse"])

    assert args.client == "pi"
    assert args.proxy_mode == "reverse"


@pytest.mark.asyncio
async def test_run_client_pi_forward_sets_proxy_ca_and_preserves_args(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}
    ca_path = Path("/tmp/test-ca.pem")

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path))
    monkeypatch.setenv("NO_PROXY", "example.com")
    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/pi")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(
        43123,
        ["--model", "openai-codex/gpt-5.3-codex-spark", "-p", "hello"],
        client="pi",
        proxy_mode="forward",
        ca_cert_path=ca_path,
    )

    assert code == 0
    assert captured["cmd"] == (
        "/tmp/pi",
        "--model",
        "openai-codex/gpt-5.3-codex-spark",
        "-p",
        "hello",
    )
    env = captured["env"]
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:43123"
    assert env["http_proxy"] == "http://127.0.0.1:43123"
    assert env["NODE_USE_ENV_PROXY"] == "1"
    assert env["NODE_EXTRA_CA_CERTS"] == str(ca_path)
    assert env["SSL_CERT_FILE"] == str(ca_path)
    assert "example.com" in env["NO_PROXY"]
    assert "localhost" in env["NO_PROXY"]
    assert "127.0.0.1" in env["NO_PROXY"]
    assert env["no_proxy"] == env["NO_PROXY"]
    assert "OPENAI_BASE_URL" not in env


@pytest.mark.asyncio
async def test_run_client_pi_reverse_sets_openai_base_url_without_codex_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path))
    monkeypatch.delenv("PI_CODING_AGENT_SESSION_DIR", raising=False)
    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/pi")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(43123, ["--provider", "openai", "-p", "hello"], client="pi", proxy_mode="reverse")

    assert code == 0
    assert captured["cmd"] == ("/tmp/pi", "--provider", "openai", "-p", "hello")
    assert captured["env"]["OPENAI_BASE_URL"] == "http://127.0.0.1:43123/v1"
    assert captured["env"]["PI_CODING_AGENT_SESSION_DIR"] == str(tmp_path / "sessions")


def _write_pi_agent(agent_dir: Path, models: dict[str, object]) -> None:
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "models.json").write_text(json.dumps(models), encoding="utf-8")
    (agent_dir / "settings.json").write_text('{\n  "theme": "dark"\n}\n', encoding="utf-8")
    (agent_dir / "auth.json").write_text('{\n  "openai-codex": {}\n}\n', encoding="utf-8")
    (agent_dir / "extensions").mkdir()
    (agent_dir / "extensions" / "demo.js").write_text("export {}", encoding="utf-8")
    (agent_dir / "skills").mkdir()


def _pi_models() -> dict[str, object]:
    return {
        "providers": {
            "cliproxy": {
                "baseUrl": "http://127.0.0.1:2317/v1",
                "apiKey": "secret",
                "models": [
                    {"id": "match", "baseUrl": "http://localhost:2317/v1"},
                    {"id": "other-port", "baseUrl": "http://127.0.0.1:9999/v1"},
                ],
            },
            "remote": {"baseUrl": "https://api.openai.com/v1", "models": [{"id": "gpt"}]},
            "localhost-gateway": {"baseUrl": "http://localhost:2317/v1", "models": []},
            "broken": "not-a-provider",
        }
    }


def _install_pi_launcher(monkeypatch: pytest.MonkeyPatch, captured: dict[str, object]) -> None:
    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/pi")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)


@pytest.mark.asyncio
async def test_pi_reverse_sandbox_contents_before_cleanup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    agent_dir = tmp_path / "agent"
    _write_pi_agent(agent_dir, _pi_models())
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent_dir))
    monkeypatch.setenv("PI_CODING_AGENT_SESSION_DIR", str(tmp_path / "custom-sessions"))
    seen: dict[str, object] = {}

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        env = kwargs["env"]
        assert isinstance(env, dict)
        sandbox_agent = Path(str(env["PI_CODING_AGENT_DIR"]))
        seen["sandbox_agent"] = sandbox_agent
        seen["models"] = json.loads((sandbox_agent / "models.json").read_text(encoding="utf-8"))
        seen["root_mode"] = stat.S_IMODE(sandbox_agent.parent.stat().st_mode)
        seen["models_mode"] = stat.S_IMODE((sandbox_agent / "models.json").stat().st_mode)
        seen["auth_mode"] = stat.S_IMODE((sandbox_agent / "auth.json").stat().st_mode)
        seen["settings"] = (sandbox_agent / "settings.json").read_text(encoding="utf-8")
        seen["extensions_link"] = (sandbox_agent / "extensions").is_symlink()
        seen["extensions_target"] = os.readlink(sandbox_agent / "extensions")
        seen["skills_link"] = (sandbox_agent / "skills").is_symlink()
        seen["prompts_absent"] = not (sandbox_agent / "prompts").exists()
        seen["session_dir"] = str(env["PI_CODING_AGENT_SESSION_DIR"])
        return _DummyProc()

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/pi")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(
        43123,
        [],
        client="pi",
        proxy_mode="reverse",
        target="http://127.0.0.1:2317",
    )

    assert code == 0
    models = seen["models"]
    assert isinstance(models, dict)
    providers = models["providers"]
    assert isinstance(providers, dict)
    cliproxy = providers["cliproxy"]
    assert isinstance(cliproxy, dict)
    cliproxy_models = cliproxy["models"]
    assert isinstance(cliproxy_models, list)
    assert cliproxy["baseUrl"] == "http://127.0.0.1:43123/v1"
    assert cliproxy["apiKey"] == "secret"
    assert cliproxy_models[0]["baseUrl"] == "http://127.0.0.1:43123/v1"
    assert cliproxy_models[1]["baseUrl"] == "http://127.0.0.1:9999/v1"
    remote = providers["remote"]
    assert isinstance(remote, dict)
    assert remote["baseUrl"] == "https://api.openai.com/v1"
    localhost_gateway = providers["localhost-gateway"]
    assert isinstance(localhost_gateway, dict)
    assert localhost_gateway["baseUrl"] == "http://127.0.0.1:43123/v1"
    assert seen["root_mode"] == 0o700
    assert seen["models_mode"] == 0o600
    assert seen["auth_mode"] == 0o600
    settings = seen["settings"]
    assert isinstance(settings, str)
    assert "dark" in settings
    assert seen["extensions_link"] is True
    assert Path(str(seen["extensions_target"])) == agent_dir / "extensions"
    assert seen["skills_link"] is True
    assert seen["prompts_absent"] is True
    assert seen["session_dir"] == str(tmp_path / "custom-sessions")
    assert not Path(str(seen["sandbox_agent"])).exists()
    assert (agent_dir / "models.json").read_text(encoding="utf-8").count("2317") == 3


def test_rewrite_pi_models_payload_matches_host_port_only() -> None:
    payload = _pi_models()
    changed = _rewrite_pi_models_payload(payload, "http://localhost:2317/v1", "http://127.0.0.1:43123/v1")

    assert changed == 3
    providers = payload["providers"]
    assert isinstance(providers, dict)
    cliproxy = providers["cliproxy"]
    assert isinstance(cliproxy, dict)
    cliproxy_models = cliproxy["models"]
    assert isinstance(cliproxy_models, list)
    localhost_gateway = providers["localhost-gateway"]
    assert isinstance(localhost_gateway, dict)
    assert cliproxy["baseUrl"] == "http://127.0.0.1:43123/v1"
    assert cliproxy_models[0]["baseUrl"] == "http://127.0.0.1:43123/v1"
    assert cliproxy_models[1]["baseUrl"] == "http://127.0.0.1:9999/v1"
    assert localhost_gateway["baseUrl"] == "http://127.0.0.1:43123/v1"
    assert _rewrite_pi_models_payload(["nope"], "http://127.0.0.1:2317", "http://127.0.0.1:1/v1") == 0
    assert _rewrite_pi_models_payload({"providers": []}, "http://127.0.0.1:2317", "http://127.0.0.1:1/v1") == 0


@pytest.mark.asyncio
async def test_pi_reverse_sandbox_cleaned_up_when_launch_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    agent_dir = tmp_path / "agent"
    _write_pi_agent(agent_dir, _pi_models())
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent_dir))
    seen: dict[str, str] = {}

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        seen["agent"] = kwargs["env"]["PI_CODING_AGENT_DIR"]
        raise OSError("launch failed")

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/pi")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    with pytest.raises(OSError, match="launch failed"):
        await run_client(43123, [], client="pi", proxy_mode="reverse", target="http://127.0.0.1:2317/v1")

    sandbox_agent = Path(seen["agent"])
    assert not sandbox_agent.exists()
    assert not sandbox_agent.parent.exists()


@pytest.mark.asyncio
async def test_pi_forward_warns_on_loopback_provider_without_stopping(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    agent_dir = tmp_path / "agent"
    _write_pi_agent(agent_dir, _pi_models())
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent_dir))
    captured: dict[str, object] = {}
    _install_pi_launcher(monkeypatch, captured)

    code = await run_client(43123, ["-p", "hello"], client="pi", proxy_mode="forward", ca_cert_path=tmp_path / "ca.pem")

    assert code == 0
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:43123"
    assert env.get("PI_CODING_AGENT_DIR") == str(agent_dir)
    warning = capsys.readouterr().err
    assert "loopback baseUrl" in warning
    assert "http://127.0.0.1:2317/v1" in warning
    assert "http://localhost:2317/v1" in warning
    assert "--tap-proxy-mode reverse --tap-target http://127.0.0.1:2317/v1" in warning


@pytest.mark.asyncio
async def test_pi_forward_silent_without_loopback_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    agent_dir = tmp_path / "agent"
    _write_pi_agent(agent_dir, {"providers": {"remote": {"baseUrl": "https://api.openai.com/v1", "models": []}}})
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent_dir))
    captured: dict[str, object] = {}
    _install_pi_launcher(monkeypatch, captured)

    code = await run_client(43123, [], client="pi", proxy_mode="forward")

    assert code == 0
    assert "loopback baseUrl" not in capsys.readouterr().err
