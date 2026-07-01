from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from claude_tap.cli import (
    _has_config_override,
    _reverse_proxy_trace_options,
    _toml_dotted_key_segment,
    parse_args,
    run_client,
)
from claude_tap.cli_clients import _codex_app_bundle_marker, _codex_app_process_already_running


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


@pytest.mark.asyncio
async def test_run_client_codex_reverse_injects_openai_base_url(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/codex")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("claude_tap.cli_clients._codex_selected_provider_base_url_key", lambda _: None)

    code = await run_client(43123, ["exec", "hello"], client="codex", proxy_mode="reverse")

    assert code == 0
    assert captured["cmd"] == (
        "/tmp/codex",
        "-c",
        'openai_base_url="http://127.0.0.1:43123/v1"',
        "exec",
        "hello",
    )
    assert captured["env"]["OPENAI_BASE_URL"] == "http://127.0.0.1:43123/v1"


@pytest.mark.asyncio
async def test_run_client_codex_reverse_respects_existing_openai_base_override(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        return _DummyProc()

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/codex")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("claude_tap.cli_clients._codex_selected_provider_base_url_key", lambda _: None)

    code = await run_client(
        43123,
        ["-c", 'openai_base_url="http://example.invalid/v1"', "exec", "hello"],
        client="codex",
        proxy_mode="reverse",
    )

    assert code == 0
    assert captured["cmd"] == (
        "/tmp/codex",
        "-c",
        'openai_base_url="http://example.invalid/v1"',
        "exec",
        "hello",
    )


@pytest.mark.asyncio
async def test_run_client_codex_forward_sets_rust_tls_ca_env(monkeypatch) -> None:
    captured: dict[str, object] = {}
    ca_path = Path("/tmp/test-ca.pem")

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/codex")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(43123, ["exec", "hello"], client="codex", proxy_mode="forward", ca_cert_path=ca_path)

    assert code == 0
    assert captured["env"]["HTTPS_PROXY"] == "http://127.0.0.1:43123"
    assert captured["env"]["SSL_CERT_FILE"] == str(ca_path)
    assert captured["env"]["CODEX_CA_CERTIFICATE"] == str(ca_path)


@pytest.mark.asyncio
async def test_run_client_codexapp_forward_launches_desktop_app_with_proxy_env(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    ca_path = tmp_path / "claude-tap-ca.pem"
    codex_app = tmp_path / "Codex.app" / "Contents" / "MacOS" / "Codex"
    codex_app.parent.mkdir(parents=True)
    codex_app.write_text("#!/bin/sh\n", encoding="utf-8")

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.setenv("CODEX_APP_EXECUTABLE", str(codex_app))
    monkeypatch.setattr("claude_tap.cli_clients._codex_app_process_already_running", lambda _: False)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(43123, [], client="codexapp", proxy_mode="forward", ca_cert_path=ca_path)

    assert code == 0
    assert captured["cmd"] == (str(codex_app), "--proxy-server=http://127.0.0.1:43123")
    env = captured["env"]
    assert env["HTTP_PROXY"] == "http://127.0.0.1:43123"
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:43123"
    assert env["ALL_PROXY"] == "http://127.0.0.1:43123"
    assert env["SSL_CERT_FILE"] == str(ca_path)
    assert env["CODEX_CA_CERTIFICATE"] == str(ca_path)


@pytest.mark.asyncio
async def test_run_client_codexapp_forward_preserves_existing_proxy_switch(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    codex_app = tmp_path / "Codex"
    codex_app.write_text("#!/bin/sh\n", encoding="utf-8")

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        return _DummyProc()

    monkeypatch.setenv("CODEX_APP_EXECUTABLE", str(codex_app))
    monkeypatch.setattr("claude_tap.cli_clients._codex_app_process_already_running", lambda _: False)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(
        43123,
        ["--proxy-server=http://127.0.0.1:9999"],
        client="codexapp",
        proxy_mode="forward",
    )

    assert code == 0
    assert captured["cmd"] == (str(codex_app), "--proxy-server=http://127.0.0.1:9999")


@pytest.mark.asyncio
async def test_run_client_codexapp_forward_requires_desktop_app_executable(monkeypatch) -> None:
    monkeypatch.delenv("CODEX_APP_EXECUTABLE", raising=False)
    monkeypatch.setattr("claude_tap.cli_clients._codex_app_executable_candidates", lambda: ())

    code = await run_client(43123, [], client="codexapp", proxy_mode="forward")

    assert code == 1


@pytest.mark.asyncio
async def test_run_client_codexapp_forward_rejects_already_running_app(monkeypatch, tmp_path: Path) -> None:
    codex_app = tmp_path / "Codex"
    codex_app.write_text("#!/bin/sh\n", encoding="utf-8")

    async def fail_create_subprocess_exec(*cmd, **kwargs):
        raise AssertionError(f"Codex App should not be launched when already running: {cmd}")

    monkeypatch.setenv("CODEX_APP_EXECUTABLE", str(codex_app))
    monkeypatch.delenv("CLAUDE_TAP_ALLOW_RUNNING_CODEX_APP", raising=False)
    monkeypatch.setattr("claude_tap.cli_clients._codex_app_process_already_running", lambda _: True)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail_create_subprocess_exec)

    code = await run_client(43123, [], client="codexapp", proxy_mode="forward")

    assert code == 1


@pytest.mark.asyncio
async def test_run_client_codexapp_forward_allows_running_app_with_override(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    codex_app = tmp_path / "Codex"
    codex_app.write_text("#!/bin/sh\n", encoding="utf-8")

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        return _DummyProc()

    monkeypatch.setenv("CODEX_APP_EXECUTABLE", str(codex_app))
    monkeypatch.setenv("CLAUDE_TAP_ALLOW_RUNNING_CODEX_APP", "1")
    monkeypatch.setattr("claude_tap.cli_clients._codex_app_process_already_running", lambda _: True)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(43123, [], client="codexapp", proxy_mode="forward")

    assert code == 0
    assert captured["cmd"] == (str(codex_app), "--proxy-server=http://127.0.0.1:43123")


def test_codex_app_process_detection_matches_bundle_helpers(monkeypatch, tmp_path: Path) -> None:
    codex_app = tmp_path / "Codex.app" / "Contents" / "MacOS" / "Codex"
    helper = tmp_path / "Codex.app" / "Contents" / "Frameworks" / "Codex Framework.framework" / "Helper"
    codex_app.parent.mkdir(parents=True)
    codex_app.write_text("#!/bin/sh\n", encoding="utf-8")

    class Result:
        returncode = 0
        stdout = f"123 {helper} --type=renderer\n456 /usr/bin/other\n"

    monkeypatch.setattr("claude_tap.cli_clients.sys.platform", "darwin")
    monkeypatch.setattr("claude_tap.cli_clients.subprocess.run", lambda *_, **__: Result())
    monkeypatch.setattr("claude_tap.cli_clients.os.getpid", lambda: 999)

    assert _codex_app_process_already_running(str(codex_app)) is True


def test_codex_app_process_detection_skips_non_macos(monkeypatch) -> None:
    monkeypatch.setattr("claude_tap.cli_clients.sys.platform", "linux")
    monkeypatch.setattr(
        "claude_tap.cli_clients.subprocess.run",
        lambda *_, **__: (_ for _ in ()).throw(AssertionError("ps should not run outside macOS")),
    )

    assert _codex_app_process_already_running("/Applications/Codex.app/Contents/MacOS/Codex") is False


def test_codex_app_process_detection_ignores_ps_failures(monkeypatch) -> None:
    monkeypatch.setattr("claude_tap.cli_clients.sys.platform", "darwin")
    monkeypatch.setattr(
        "claude_tap.cli_clients.subprocess.run",
        lambda *_, **__: (_ for _ in ()).throw(OSError("ps unavailable")),
    )

    assert _codex_app_process_already_running("/Applications/Codex.app/Contents/MacOS/Codex") is False


def test_codex_app_process_detection_ignores_nonzero_ps(monkeypatch) -> None:
    class Result:
        returncode = 1
        stdout = ""

    monkeypatch.setattr("claude_tap.cli_clients.sys.platform", "darwin")
    monkeypatch.setattr("claude_tap.cli_clients.subprocess.run", lambda *_, **__: Result())

    assert _codex_app_process_already_running("/Applications/Codex.app/Contents/MacOS/Codex") is False


def test_codex_app_process_detection_ignores_bad_and_current_process_lines(monkeypatch, tmp_path: Path) -> None:
    codex_app = tmp_path / "Codex.app" / "Contents" / "MacOS" / "Codex"
    codex_app.parent.mkdir(parents=True)
    codex_app.write_text("#!/bin/sh\n", encoding="utf-8")

    class Result:
        returncode = 0
        stdout = f"not-a-pid {codex_app}\n777 {codex_app}\n888 /usr/bin/other\n"

    monkeypatch.setattr("claude_tap.cli_clients.sys.platform", "darwin")
    monkeypatch.setattr("claude_tap.cli_clients.subprocess.run", lambda *_, **__: Result())
    monkeypatch.setattr("claude_tap.cli_clients.os.getpid", lambda: 777)

    assert _codex_app_process_already_running(str(codex_app)) is False


def test_codex_app_bundle_marker_returns_none_for_plain_executable(tmp_path: Path) -> None:
    executable = tmp_path / "Codex"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")

    assert _codex_app_bundle_marker(str(executable)) is None


def test_has_config_override_detects_cli_forms() -> None:
    assert _has_config_override(["-c", 'openai_base_url="http://127.0.0.1:1/v1"'], "openai_base_url") is True
    assert _has_config_override(["--config", 'openai_base_url="http://127.0.0.1:1/v1"'], "openai_base_url") is True
    assert _has_config_override(['--config=openai_base_url="http://127.0.0.1:1/v1"'], "openai_base_url") is True
    assert _has_config_override(["exec", "hello"], "openai_base_url") is False
    assert (
        _has_config_override(
            ["-c", 'model_providers.newapi.base_url="http://127.0.0.1:1/v1"'],
            "model_providers.newapi.base_url",
        )
        is True
    )


def test_toml_dotted_key_segment_quotes_non_ascii_provider_ids() -> None:
    assert _toml_dotted_key_segment("newapi") == "newapi"
    assert _toml_dotted_key_segment("new-api") == "new-api"
    assert _toml_dotted_key_segment("new.api") == '"new.api"'
    assert _toml_dotted_key_segment("模型") == '"\\u6a21\\u578b"'


def test_parse_args_codex_auto_detects_chatgpt_target(monkeypatch, tmp_path) -> None:
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text('{"auth_mode":"chatgpt"}\n', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    args = parse_args(["--tap-client", "codex"])

    assert args.target == "https://chatgpt.com/backend-api/codex"


def test_parse_args_codex_auto_detects_custom_provider_target(monkeypatch, tmp_path) -> None:
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text('{"OPENAI_API_KEY":"sk-test"}\n', encoding="utf-8")
    (codex_home / "config.toml").write_text(
        "\n".join(
            [
                'model = "gpt-5.4"',
                'model_provider = "newapi"',
                "",
                "[model_providers.newapi]",
                'base_url = "https://new-api.example.test/v1"',
                'name = "Custom"',
                "requires_openai_auth = true",
                'wire_api = "responses"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    args = parse_args(["--tap-client", "codex"])

    assert args.target == "https://new-api.example.test/v1"


def test_parse_args_codex_custom_provider_precedes_openai_base_url_env(monkeypatch, tmp_path) -> None:
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text('{"OPENAI_API_KEY":"sk-test"}\n', encoding="utf-8")
    (codex_home / "config.toml").write_text(
        "\n".join(
            [
                'model_provider = "newapi"',
                "",
                "[model_providers.newapi]",
                'base_url = "https://new-api.example.test/v1"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("OPENAI_BASE_URL", "https://stale-env.example.test/v1")

    args = parse_args(["--tap-client", "codex"])

    assert args.target == "https://new-api.example.test/v1"


def test_parse_args_codex_auto_detects_custom_provider_from_profile(monkeypatch, tmp_path) -> None:
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text('{"OPENAI_API_KEY":"sk-test"}\n', encoding="utf-8")
    (codex_home / "config.toml").write_text(
        "\n".join(
            [
                'model_provider = "openai"',
                "",
                "[profiles.staging]",
                'model_provider = "newapi"',
                "",
                "[model_providers.openai]",
                'base_url = "https://api.openai.com/v1"',
                "",
                "[model_providers.newapi]",
                'base_url = "https://new-api.example.test/v1"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    args = parse_args(["--tap-client", "codex", "--", "--profile", "staging"])

    assert args.target == "https://new-api.example.test/v1"


def test_parse_args_codex_auto_detects_custom_provider_from_model_provider_override(monkeypatch, tmp_path) -> None:
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text('{"OPENAI_API_KEY":"sk-test"}\n', encoding="utf-8")
    (codex_home / "config.toml").write_text(
        "\n".join(
            [
                'model_provider = "openai"',
                "",
                "[model_providers.openai]",
                'base_url = "https://api.openai.com/v1"',
                "",
                "[model_providers.newapi]",
                'base_url = "https://new-api.example.test/v1"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    args = parse_args(["--tap-client", "codex", "--", "-c", 'model_provider="newapi"'])

    assert args.target == "https://new-api.example.test/v1"


@pytest.mark.asyncio
async def test_run_client_codex_reverse_injects_selected_provider_base_url(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        "\n".join(
            [
                'model_provider = "newapi"',
                "",
                "[model_providers.newapi]",
                'base_url = "https://new-api.example.test/v1"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/codex")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(43123, ["exec", "hello"], client="codex", proxy_mode="reverse")

    assert code == 0
    assert captured["cmd"] == (
        "/tmp/codex",
        "-c",
        'openai_base_url="http://127.0.0.1:43123/v1"',
        "-c",
        'model_providers.newapi.base_url="http://127.0.0.1:43123/v1"',
        "exec",
        "hello",
    )
    assert captured["env"]["OPENAI_BASE_URL"] == "http://127.0.0.1:43123/v1"


@pytest.mark.asyncio
async def test_run_client_codex_reverse_injects_profile_provider_base_url(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        "\n".join(
            [
                'model_provider = "openai"',
                "",
                "[profiles.staging]",
                'model_provider = "newapi"',
                "",
                "[model_providers.openai]",
                'base_url = "https://api.openai.com/v1"',
                "",
                "[model_providers.newapi]",
                'base_url = "https://new-api.example.test/v1"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        return _DummyProc()

    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/codex")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(43123, ["--profile", "staging", "exec", "hello"], client="codex", proxy_mode="reverse")

    assert code == 0
    assert captured["cmd"] == (
        "/tmp/codex",
        "-c",
        'openai_base_url="http://127.0.0.1:43123/v1"',
        "-c",
        'model_providers.newapi.base_url="http://127.0.0.1:43123/v1"',
        "--profile",
        "staging",
        "exec",
        "hello",
    )


@pytest.mark.asyncio
async def test_run_client_codex_reverse_injects_provider_from_model_provider_override(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        "\n".join(
            [
                'model_provider = "openai"',
                "",
                "[model_providers.openai]",
                'base_url = "https://api.openai.com/v1"',
                "",
                "[model_providers.newapi]",
                'base_url = "https://new-api.example.test/v1"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        return _DummyProc()

    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/codex")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(
        43123,
        ["-c", 'model_provider="newapi"', "exec", "hello"],
        client="codex",
        proxy_mode="reverse",
    )

    assert code == 0
    assert captured["cmd"] == (
        "/tmp/codex",
        "-c",
        'openai_base_url="http://127.0.0.1:43123/v1"',
        "-c",
        'model_providers.newapi.base_url="http://127.0.0.1:43123/v1"',
        "-c",
        'model_provider="newapi"',
        "exec",
        "hello",
    )


@pytest.mark.asyncio
async def test_run_client_codex_reverse_quotes_non_ascii_provider_base_url_key(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        "\n".join(
            [
                'model_provider = "模型"',
                "",
                '[model_providers."模型"]',
                'base_url = "https://new-api.example.test/v1"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        return _DummyProc()

    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/codex")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(43123, ["exec", "hello"], client="codex", proxy_mode="reverse")

    assert code == 0
    assert captured["cmd"] == (
        "/tmp/codex",
        "-c",
        'openai_base_url="http://127.0.0.1:43123/v1"',
        "-c",
        'model_providers."\\u6a21\\u578b".base_url="http://127.0.0.1:43123/v1"',
        "exec",
        "hello",
    )


@pytest.mark.asyncio
async def test_run_client_codex_reverse_respects_existing_selected_provider_override(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        "\n".join(
            [
                'model_provider = "newapi"',
                "",
                "[model_providers.newapi]",
                'base_url = "https://new-api.example.test/v1"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        return _DummyProc()

    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/codex")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(
        43123,
        ["-c", 'model_providers.newapi.base_url="http://example.invalid/v1"', "exec", "hello"],
        client="codex",
        proxy_mode="reverse",
    )

    assert code == 0
    assert captured["cmd"] == (
        "/tmp/codex",
        "-c",
        'openai_base_url="http://127.0.0.1:43123/v1"',
        "-c",
        'model_providers.newapi.base_url="http://example.invalid/v1"',
        "exec",
        "hello",
    )


def test_parse_args_claude_uses_env_base_url(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.example.test/v1/anthropic")

    args = parse_args([])

    assert args.target == "https://gateway.example.test/v1/anthropic"


def test_parse_args_claude_uses_project_settings_base_url(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    home = tmp_path / "home"
    project = tmp_path / "project"
    home_settings = home / ".claude"
    project_settings = project / ".claude"
    home_settings.mkdir(parents=True)
    project_settings.mkdir(parents=True)
    (home_settings / "settings.json").write_text(
        '{"env":{"ANTHROPIC_BASE_URL":"https://global.example.test/v1/anthropic"}}\n',
        encoding="utf-8",
    )
    (project_settings / "settings.local.json").write_text(
        '{"env":{"ANTHROPIC_BASE_URL":"https://project.example.test/v1/anthropic"}}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    monkeypatch.chdir(project)

    args = parse_args([])

    assert args.target == "https://project.example.test/v1/anthropic"


def test_parse_args_claude_falls_back_to_default_target(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
    monkeypatch.chdir(tmp_path)

    args = parse_args([])

    assert args.target == "https://api.anthropic.com"


def test_codex_reverse_trace_options_allow_websocket() -> None:
    options = _reverse_proxy_trace_options("codex", "https://chatgpt.com/backend-api/codex")

    assert options == {
        "strip_path_prefix": "/v1",
        "force_http": False,
    }
