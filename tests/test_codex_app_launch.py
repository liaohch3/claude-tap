from __future__ import annotations

import asyncio
import os
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from claude_tap import cli_clients
from claude_tap.cli import run_client


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


def _write_app_plist(app_root: Path, bundle_id: str) -> None:
    (app_root / "Contents").mkdir(parents=True, exist_ok=True)
    (app_root / "Contents" / "Info.plist").write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>{bundle_id}</string>
</dict></plist>
"""
    )


def test_codex_app_existing_processes_filters_current_pid_and_legacy_chatgpt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    current_pid = os.getpid()
    chatgpt_app = tmp_path / "ChatGPT.app"
    _write_app_plist(chatgpt_app, "com.openai.codex")
    chatgpt_bin = chatgpt_app / "Contents" / "MacOS" / "ChatGPT"
    chatgpt_bin.parent.mkdir(parents=True)
    chatgpt_bin.write_text("")
    chatgpt_codex = chatgpt_app / "Contents" / "Resources" / "codex"
    chatgpt_codex.parent.mkdir(parents=True)
    chatgpt_codex.write_text("")

    legacy_app = tmp_path / "LegacyChatGPT.app"
    _write_app_plist(legacy_app, "com.openai.chat")
    legacy_bin = legacy_app / "Contents" / "MacOS" / "ChatGPT"
    legacy_bin.parent.mkdir(parents=True)
    legacy_bin.write_text("")

    def fake_run(cmd: list[str], **kwargs: object) -> SimpleNamespace:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout=(
                f"{current_pid} {chatgpt_bin}\n"
                f"123 {chatgpt_codex}\n"
                "124 /Applications/Codex.app/Contents/Resources/codex app-server\n"
                f"125 {legacy_bin}\n"
            ),
        )

    monkeypatch.setattr(cli_clients.sys, "platform", "darwin")
    monkeypatch.setattr(cli_clients.subprocess, "run", fake_run)

    assert cli_clients._codex_app_existing_processes() == [
        f"123 {chatgpt_codex}",
        "124 /Applications/Codex.app/Contents/Resources/codex app-server",
    ]
    assert captured["cmd"] == ["pgrep", "-fl", f"({cli_clients._CODEX_APP_PROCESS_RE})"]
    assert captured["kwargs"]["timeout"] == 2


def test_codex_app_isolated_profile_dir_is_unique_per_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("CODEX_APP_USER_DATA_DIR", raising=False)
    monkeypatch.setattr(cli_clients, "_CODEX_APP_ISOLATED_PROFILE_ROOT", tmp_path / "profiles")

    first = cli_clients._codex_app_isolated_profile_dir()
    second = cli_clients._codex_app_isolated_profile_dir()

    assert first != second
    assert first.parent == tmp_path / "profiles"
    assert first.name.startswith("tap-")
    assert second.name.startswith("tap-")


def test_codex_app_existing_processes_matches_custom_executable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configured = tmp_path / "Codex Dev"
    captured: dict[str, object] = {}

    def fake_run(cmd: list[str], **_kwargs: object) -> SimpleNamespace:
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout=f"123 {configured}\n")

    monkeypatch.setattr(cli_clients.sys, "platform", "darwin")
    monkeypatch.setenv(cli_clients._CODEX_APP_EXECUTABLE_ENV, str(configured))
    monkeypatch.setattr(cli_clients.subprocess, "run", fake_run)

    assert cli_clients._codex_app_existing_processes() == [f"123 {configured}"]
    assert captured["cmd"] == [
        "pgrep",
        "-fl",
        f"({cli_clients._CODEX_APP_PROCESS_RE}|{re.escape(str(configured))})",
    ]


def test_codex_app_existing_processes_handles_unsupported_platform_and_pgrep_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_clients.sys, "platform", "linux")
    assert cli_clients._codex_app_existing_processes() == []

    monkeypatch.setattr(cli_clients.sys, "platform", "darwin")
    monkeypatch.setattr(
        cli_clients.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=2, stdout="")
    )
    assert cli_clients._codex_app_existing_processes() == []

    def raise_os_error(*_args: object, **_kwargs: object) -> SimpleNamespace:
        raise OSError("pgrep unavailable")

    monkeypatch.setattr(cli_clients.subprocess, "run", raise_os_error)
    assert cli_clients._codex_app_existing_processes() == []


def test_quit_codex_app_uses_bundle_id_and_reports_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(cmd: list[str], **kwargs: object) -> SimpleNamespace:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(cli_clients.subprocess, "run", fake_run)

    assert cli_clients._quit_codex_app() is True
    assert captured["cmd"] == ["osascript", "-e", 'tell application id "com.openai.codex" to quit']
    assert captured["kwargs"]["timeout"] == 5

    monkeypatch.setattr(cli_clients.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=1))
    assert cli_clients._quit_codex_app() is False

    def raise_subprocess_error(*_args: object, **_kwargs: object) -> SimpleNamespace:
        raise subprocess.SubprocessError("osascript failed")

    monkeypatch.setattr(cli_clients.subprocess, "run", raise_subprocess_error)
    assert cli_clients._quit_codex_app() is False


def test_codex_app_executable_candidates_prefers_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli_clients.sys, "platform", "darwin")
    monkeypatch.setenv(cli_clients._CODEX_APP_EXECUTABLE_ENV, "~/custom/Codex")

    candidates = cli_clients._codex_app_executable_candidates()

    assert candidates[0] == Path("~/custom/Codex").expanduser()
    assert Path("/Applications/ChatGPT.app/Contents/MacOS/ChatGPT") in candidates
    assert Path("/Applications/Codex.app/Contents/MacOS/Codex") in candidates
    assert Path.home() / "Applications/ChatGPT.app/Contents/MacOS/ChatGPT" in candidates
    assert Path.home() / "Applications/Codex.app/Contents/MacOS/Codex" in candidates
    # Prefer the current ChatGPT.app host over the legacy Codex.app bundle.
    assert candidates.index(Path("/Applications/ChatGPT.app/Contents/MacOS/ChatGPT")) < candidates.index(
        Path("/Applications/Codex.app/Contents/MacOS/Codex")
    )


def test_codex_app_process_re_matches_chatgpt_and_legacy_codex_paths() -> None:
    pattern = re.compile(cli_clients._CODEX_APP_PROCESS_RE)
    assert pattern.search("/Applications/ChatGPT.app/Contents/MacOS/ChatGPT")
    assert pattern.search("/Applications/ChatGPT.app/Contents/Resources/codex")
    assert pattern.search("/Applications/Codex.app/Contents/MacOS/Codex")
    assert pattern.search("/Applications/Codex.app/Contents/Resources/codex app-server")
    assert not pattern.search("/Applications/Safari.app/Contents/MacOS/Safari")
    # pgrep on macOS uses POSIX ERE and rejects Python-style non-capturing groups.
    assert "(?:" not in cli_clients._CODEX_APP_PROCESS_RE


def test_is_codex_desktop_executable_requires_codex_bundle_id_for_chatgpt_app(
    tmp_path: Path,
) -> None:
    chatgpt = tmp_path / "ChatGPT.app" / "Contents" / "MacOS" / "ChatGPT"
    chatgpt.parent.mkdir(parents=True)
    chatgpt.write_text("")
    info = tmp_path / "ChatGPT.app" / "Contents" / "Info.plist"
    info.write_bytes(
        b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>com.openai.chat</string>
</dict></plist>
"""
    )
    assert cli_clients._is_codex_desktop_executable(chatgpt) is False

    info.write_bytes(
        b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>com.openai.codex</string>
</dict></plist>
"""
    )
    assert cli_clients._is_codex_desktop_executable(chatgpt) is True

    legacy = tmp_path / "Codex.app" / "Contents" / "MacOS" / "Codex"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("")
    assert cli_clients._is_codex_desktop_executable(legacy) is True


def test_resolve_client_executable_skips_non_codex_chatgpt_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    wrong_chatgpt = tmp_path / "ChatGPT.app" / "Contents" / "MacOS" / "ChatGPT"
    wrong_chatgpt.parent.mkdir(parents=True)
    wrong_chatgpt.write_text("")
    (tmp_path / "ChatGPT.app" / "Contents" / "Info.plist").write_bytes(
        b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>com.openai.chat</string>
</dict></plist>
"""
    )
    legacy = tmp_path / "Codex.app" / "Contents" / "MacOS" / "Codex"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("")
    monkeypatch.delenv(cli_clients._CODEX_APP_EXECUTABLE_ENV, raising=False)
    monkeypatch.setattr(
        cli_clients,
        "_codex_app_executable_candidates",
        lambda: (wrong_chatgpt, legacy),
    )

    cfg = cli_clients.CLIENT_CONFIGS["codexapp"]
    assert cli_clients._resolve_client_executable("codexapp", cfg, None) == str(legacy)


def test_codex_app_executable_candidates_empty_on_non_macos_without_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_clients.sys, "platform", "linux")
    monkeypatch.delenv(cli_clients._CODEX_APP_EXECUTABLE_ENV, raising=False)

    assert cli_clients._codex_app_executable_candidates() == ()


def test_resolve_client_executable_uses_env_override_before_default_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configured = tmp_path / "Codex"
    configured.write_text("")
    monkeypatch.setenv(cli_clients._CODEX_APP_EXECUTABLE_ENV, str(configured))
    monkeypatch.setattr(
        cli_clients,
        "_codex_app_executable_candidates",
        lambda: (configured, Path("/Applications/Codex.app/Contents/MacOS/Codex")),
    )

    cfg = cli_clients.CLIENT_CONFIGS["codexapp"]
    resolved = cli_clients._resolve_client_executable("codexapp", cfg, None)

    assert resolved == str(configured)


def test_resolve_client_executable_returns_none_when_no_candidate_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_clients, "_codex_app_executable_candidates", lambda: (Path("/nonexistent/Codex"),))

    cfg = cli_clients.CLIENT_CONFIGS["codexapp"]
    assert cli_clients._resolve_client_executable("codexapp", cfg, None) is None


def test_resolve_client_executable_prefers_explicit_client_cmd(tmp_path: Path) -> None:
    wrapper_cmd = tmp_path / "codex-wrapper"
    wrapper_cmd.write_text("")

    cfg = cli_clients.CLIENT_CONFIGS["codexapp"]
    resolved = cli_clients._resolve_client_executable("codexapp", cfg, str(wrapper_cmd))

    assert resolved == str(wrapper_cmd)


def test_resolve_client_executable_falls_back_to_path_lookup_for_other_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_clients.shutil, "which", lambda cmd: f"/usr/local/bin/{cmd}")

    cfg = cli_clients.CLIENT_CONFIGS["claude"]
    resolved = cli_clients._resolve_client_executable("claude", cfg, None)

    assert resolved == "/usr/local/bin/claude"


@pytest.mark.asyncio
async def test_wait_for_codex_app_exit_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_clients, "_codex_app_existing_processes", lambda: ["123 Codex"])

    async def fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    assert await cli_clients._wait_for_codex_app_exit(timeout_seconds=0) is False


@pytest.mark.asyncio
async def test_prepare_codex_app_forward_launch_uses_isolated_profile_when_already_running(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    profile = tmp_path / "isolated-profile"
    monkeypatch.setattr(
        cli_clients,
        "_codex_app_existing_processes",
        lambda: [
            "123 /Applications/Codex.app/Contents/MacOS/Codex",
            "124 /Applications/Codex.app/Contents/Resources/codex app-server",
            "125 /Applications/Codex.app/Contents/Resources/codex app-server",
            "126 /Applications/Codex.app/Contents/Resources/codex app-server",
        ],
    )
    monkeypatch.setattr(cli_clients, "_codex_app_isolated_profile_dir", lambda: profile)

    plan = await cli_clients._prepare_codex_app_forward_launch()

    assert plan.proceed is True
    assert plan.user_data_dir == profile
    assert profile.is_dir()
    out = capsys.readouterr().err
    assert "already running" in out
    assert "isolated second instance" in out
    assert str(profile) in out


@pytest.mark.asyncio
async def test_prepare_codex_app_forward_launch_keeps_default_profile_when_idle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_clients, "_codex_app_existing_processes", lambda: [])

    plan = await cli_clients._prepare_codex_app_forward_launch()

    assert plan == cli_clients.CodexAppLaunchPlan(proceed=True, user_data_dir=None)


@pytest.mark.asyncio
async def test_prepare_codex_app_forward_launch_forces_isolated_profile_from_env(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    profile = tmp_path / "forced-profile"
    monkeypatch.setenv("CODEX_APP_USER_DATA_DIR", str(profile))
    monkeypatch.setattr(cli_clients, "_codex_app_existing_processes", lambda: [])
    monkeypatch.setattr(cli_clients, "_codex_app_isolated_profile_dir", lambda: profile)

    plan = await cli_clients._prepare_codex_app_forward_launch()

    assert plan.proceed is True
    assert plan.user_data_dir == profile
    assert profile.is_dir()
    out = capsys.readouterr().err
    assert "CODEX_APP_USER_DATA_DIR" in out
    assert str(profile) in out


@pytest.mark.asyncio
async def test_run_client_codexapp_forward_launches_app_with_proxy_env(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, object] = {}
    ca_path = Path("/tmp/test-ca.pem")

    async def fake_create_subprocess_exec(*cmd: str, **kwargs: object) -> _DummyProc:
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        captured["stdin"] = kwargs["stdin"]
        captured["stdout"] = kwargs["stdout"]
        captured["stderr"] = kwargs["stderr"]
        return _DummyProc()

    monkeypatch.setattr(
        "claude_tap.cli_clients._resolve_client_executable",
        lambda client, cfg, client_cmd: "/Applications/Codex.app/Contents/MacOS/Codex",
    )
    monkeypatch.setattr("claude_tap.cli_clients._codex_app_existing_processes", lambda: [])
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(
        43123,
        [],
        client="codexapp",
        proxy_mode="forward",
        ca_cert_path=ca_path,
    )

    env = captured["env"]
    assert code == 0
    assert captured["cmd"] == (
        "/Applications/Codex.app/Contents/MacOS/Codex",
        "--proxy-server=http://127.0.0.1:43123",
    )
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:43123"
    assert env["SSL_CERT_FILE"] == str(ca_path)
    assert env["CODEX_CA_CERTIFICATE"] == str(ca_path)
    assert captured["stdin"] == subprocess.DEVNULL
    assert captured["stdout"] == subprocess.DEVNULL
    assert captured["stderr"] == subprocess.DEVNULL
    out = capsys.readouterr().err
    assert "Codex App exited immediately" in out
    assert "already-running Codex/ChatGPT App" in out


@pytest.mark.asyncio
async def test_run_client_codexapp_forward_launches_isolated_instance_when_app_running(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    profile = tmp_path / "tap-profile"

    async def fake_create_subprocess_exec(*cmd: str, **kwargs: object) -> _DummyProc:
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.setattr(
        "claude_tap.cli_clients._resolve_client_executable",
        lambda client, cfg, client_cmd: "/Applications/ChatGPT.app/Contents/MacOS/ChatGPT",
    )
    monkeypatch.setattr(
        "claude_tap.cli_clients._codex_app_existing_processes",
        lambda: ["123 /Applications/ChatGPT.app/Contents/MacOS/ChatGPT"],
    )
    monkeypatch.setattr("claude_tap.cli_clients._codex_app_isolated_profile_dir", lambda: profile)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    code = await run_client(43123, [], client="codexapp", proxy_mode="forward")

    assert code == 0
    assert captured["cmd"] == (
        "/Applications/ChatGPT.app/Contents/MacOS/ChatGPT",
        f"--user-data-dir={profile}",
        "--proxy-server=http://127.0.0.1:43123",
    )
    assert profile.is_dir()
    out = capsys.readouterr().err
    assert "isolated second instance" in out


@pytest.mark.asyncio
async def test_run_client_codexapp_forward_respects_preflighted_isolated_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    profile = tmp_path / "preflight-profile"
    prepare_called = False

    async def fake_create_subprocess_exec(*cmd: str, **_kwargs: object) -> _DummyProc:
        captured["cmd"] = cmd
        return _DummyProc()

    async def fail_prepare() -> cli_clients.CodexAppLaunchPlan:
        nonlocal prepare_called
        prepare_called = True
        raise AssertionError("prepare should be skipped when preflighted")

    monkeypatch.setattr(
        "claude_tap.cli_clients._resolve_client_executable",
        lambda client, cfg, client_cmd: "/Applications/Codex.app/Contents/MacOS/Codex",
    )
    monkeypatch.setattr("claude_tap.cli_clients._prepare_codex_app_forward_launch", fail_prepare)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    code = await run_client(
        43123,
        [],
        client="codexapp",
        proxy_mode="forward",
        codex_app_preflighted=True,
        codex_app_user_data_dir=profile,
    )

    assert code == 0
    assert prepare_called is False
    assert captured["cmd"] == (
        "/Applications/Codex.app/Contents/MacOS/Codex",
        f"--user-data-dir={profile}",
        "--proxy-server=http://127.0.0.1:43123",
    )
