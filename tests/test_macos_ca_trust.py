from __future__ import annotations

import subprocess
from argparse import Namespace
from pathlib import Path

import pytest

from claude_tap import parse_args
from claude_tap.certs import (
    build_macos_trust_ca_command,
    build_macos_verify_ca_command,
    is_macos_ca_trusted,
    trust_macos_ca,
)
from claude_tap.cli import _ensure_ca_trust_for_forward_proxy, _trust_ca_for_current_user, async_main, trust_ca_main


def test_parse_args_accepts_tap_trust_ca() -> None:
    args = parse_args(["--tap-client", "agy", "--tap-trust-ca"])

    assert args.client == "agy"
    assert args.proxy_mode == "forward"
    assert args.trust_ca is True


def test_parse_args_agy_does_not_require_tap_trust_ca() -> None:
    args = parse_args(["--tap-client", "agy"])

    assert args.client == "agy"
    assert args.proxy_mode == "forward"
    assert args.trust_ca is False


def test_parse_args_rejects_tap_trust_ca_with_reverse_proxy() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--tap-client", "agy", "--tap-proxy-mode", "reverse", "--tap-trust-ca"])


def test_macos_trust_ca_command_uses_user_keychain_without_sudo() -> None:
    ca_path = Path("/tmp/claude-tap-ca.pem")
    keychain_path = Path("/Users/test/Library/Keychains/login.keychain-db")

    cmd = build_macos_trust_ca_command(ca_path, keychain_path)

    assert cmd == [
        "security",
        "add-trusted-cert",
        "-r",
        "trustRoot",
        "-p",
        "ssl",
        "-k",
        str(keychain_path),
        str(ca_path),
    ]
    assert "sudo" not in cmd


def test_macos_verify_ca_command_is_non_mutating() -> None:
    ca_path = Path("/tmp/claude-tap-ca.pem")
    keychain_path = Path("/Users/test/Library/Keychains/login.keychain-db")

    cmd = build_macos_verify_ca_command(ca_path, keychain_path)

    assert cmd[0] == "security"
    assert "verify-cert" in cmd
    assert "add-trusted-cert" not in cmd
    assert str(keychain_path) in cmd


def test_is_macos_ca_trusted_reads_security_verify_result(monkeypatch: pytest.MonkeyPatch) -> None:
    ca_path = Path("/tmp/claude-tap-ca.pem")
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        assert kwargs == {"capture_output": True, "text": True, "check": False}
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("claude_tap.certs.subprocess.run", fake_run)

    assert is_macos_ca_trusted(ca_path) is True
    assert calls[0][1] == "verify-cert"


def test_is_macos_ca_trusted_returns_false_on_verify_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "not trusted")

    monkeypatch.setattr("claude_tap.certs.subprocess.run", fake_run)

    assert is_macos_ca_trusted(Path("/tmp/claude-tap-ca.pem")) is False


def test_trust_macos_ca_runs_add_trusted_cert(monkeypatch: pytest.MonkeyPatch) -> None:
    ca_path = Path("/tmp/claude-tap-ca.pem")
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        assert kwargs == {"capture_output": True, "text": True, "check": False}
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    monkeypatch.setattr("claude_tap.certs.subprocess.run", fake_run)

    result = trust_macos_ca(ca_path)

    assert result.returncode == 0
    assert calls[0][1] == "add-trusted-cert"
    assert calls[0][-1] == str(ca_path)


def test_trust_ca_for_current_user_rejects_non_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("claude_tap.cli.sys.platform", "linux")

    code = _trust_ca_for_current_user(Path("/tmp/claude-tap-ca.pem"))

    assert code == 1


def test_trust_ca_for_current_user_skips_when_already_trusted(monkeypatch: pytest.MonkeyPatch) -> None:
    ca_path = Path("/tmp/claude-tap-ca.pem")

    monkeypatch.setattr("claude_tap.cli.sys.platform", "darwin")
    monkeypatch.setattr("claude_tap.cli.is_macos_ca_trusted", lambda _: True)
    monkeypatch.setattr(
        "claude_tap.cli.trust_macos_ca",
        lambda _: (_ for _ in ()).throw(AssertionError("trust command should not run")),
    )

    assert _trust_ca_for_current_user(ca_path) == 0


def test_trust_ca_for_current_user_reports_install_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    ca_path = Path("/tmp/claude-tap-ca.pem")

    monkeypatch.setattr("claude_tap.cli.sys.platform", "darwin")
    monkeypatch.setattr("claude_tap.cli.is_macos_ca_trusted", lambda _: False)
    monkeypatch.setattr(
        "claude_tap.cli.trust_macos_ca",
        lambda _: subprocess.CompletedProcess(["security"], 2, "", "keychain locked"),
    )

    assert _trust_ca_for_current_user(ca_path) == 2


def test_trust_ca_for_current_user_reports_verify_failure_after_install(monkeypatch: pytest.MonkeyPatch) -> None:
    ca_path = Path("/tmp/claude-tap-ca.pem")
    trusted_checks = iter([False, False])

    monkeypatch.setattr("claude_tap.cli.sys.platform", "darwin")
    monkeypatch.setattr("claude_tap.cli.is_macos_ca_trusted", lambda _: next(trusted_checks))
    monkeypatch.setattr(
        "claude_tap.cli.trust_macos_ca",
        lambda _: subprocess.CompletedProcess(["security"], 0, "", ""),
    )

    assert _trust_ca_for_current_user(ca_path) == 1


def test_trust_ca_for_current_user_installs_and_rechecks(monkeypatch: pytest.MonkeyPatch) -> None:
    ca_path = Path("/tmp/claude-tap-ca.pem")
    trusted_checks = iter([False, True])
    installed: list[Path] = []

    def fake_is_trusted(path: Path) -> bool:
        assert path == ca_path
        return next(trusted_checks)

    def fake_trust(path: Path) -> subprocess.CompletedProcess[str]:
        installed.append(path)
        return subprocess.CompletedProcess(["security"], 0, "", "")

    monkeypatch.setattr("claude_tap.cli.sys.platform", "darwin")
    monkeypatch.setattr("claude_tap.cli.is_macos_ca_trusted", fake_is_trusted)
    monkeypatch.setattr("claude_tap.cli.trust_macos_ca", fake_trust)

    code = _trust_ca_for_current_user(ca_path)

    assert code == 0
    assert installed == [ca_path]


def test_trust_ca_main_uses_generated_ca(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ca_path = tmp_path / "ca.pem"
    key_path = tmp_path / "ca-key.pem"
    trusted: list[Path] = []

    monkeypatch.setattr("claude_tap.cli.ensure_ca", lambda: (ca_path, key_path))
    monkeypatch.setattr("claude_tap.cli._trust_ca_for_current_user", lambda path: trusted.append(path) or 0)

    assert trust_ca_main([]) == 0
    assert trusted == [ca_path]


def test_auto_ca_trust_skips_non_agy_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    ca_path = Path("/tmp/claude-tap-ca.pem")
    args = parse_args(["--tap-client", "gemini"])

    monkeypatch.setattr("claude_tap.cli.sys.platform", "darwin")
    monkeypatch.setattr(
        "claude_tap.cli.is_macos_ca_trusted",
        lambda _: (_ for _ in ()).throw(AssertionError("non-agy clients should not auto-check CA trust")),
    )

    assert _ensure_ca_trust_for_forward_proxy(args, ca_path) == 0


def test_auto_ca_trust_skips_when_agy_ca_is_already_trusted(monkeypatch: pytest.MonkeyPatch) -> None:
    ca_path = Path("/tmp/claude-tap-ca.pem")
    args = parse_args(["--tap-client", "agy"])

    monkeypatch.setattr("claude_tap.cli.sys.platform", "darwin")
    monkeypatch.setattr("claude_tap.cli.is_macos_ca_trusted", lambda _: True)
    monkeypatch.setattr(
        "claude_tap.cli._trust_ca_for_current_user",
        lambda _: (_ for _ in ()).throw(AssertionError("already-trusted CA should not reinstall")),
    )

    assert _ensure_ca_trust_for_forward_proxy(args, ca_path) == 0


def test_auto_ca_trust_installs_for_agy_on_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    ca_path = Path("/tmp/claude-tap-ca.pem")
    args = parse_args(["--tap-client", "agy"])
    trusted: list[Path] = []

    monkeypatch.setattr("claude_tap.cli.sys.platform", "darwin")
    monkeypatch.setattr("claude_tap.cli.is_macos_ca_trusted", lambda _: False)
    monkeypatch.setattr("claude_tap.cli._trust_ca_for_current_user", lambda path: trusted.append(path) or 0)

    assert _ensure_ca_trust_for_forward_proxy(args, ca_path) == 0
    assert trusted == [ca_path]


def test_auto_ca_trust_skips_agy_on_non_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    ca_path = Path("/tmp/claude-tap-ca.pem")
    args = parse_args(["--tap-client", "agy"])

    monkeypatch.setattr("claude_tap.cli.sys.platform", "linux")
    monkeypatch.setattr(
        "claude_tap.cli.is_macos_ca_trusted",
        lambda _: (_ for _ in ()).throw(AssertionError("non-macOS should not auto-check CA trust")),
    )

    assert _ensure_ca_trust_for_forward_proxy(args, ca_path) == 0


def test_explicit_ca_trust_still_runs_for_other_forward_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    ca_path = Path("/tmp/claude-tap-ca.pem")
    args = parse_args(["--tap-client", "gemini", "--tap-trust-ca"])
    trusted: list[Path] = []

    monkeypatch.setattr("claude_tap.cli._trust_ca_for_current_user", lambda path: trusted.append(path) or 0)

    assert _ensure_ca_trust_for_forward_proxy(args, ca_path) == 0
    assert trusted == [ca_path]


@pytest.mark.asyncio
async def test_async_main_returns_before_starting_proxy_when_trust_ca_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ca_path = tmp_path / "ca.pem"
    key_path = tmp_path / "ca-key.pem"
    proxy_started = False

    def fail_if_proxy_starts(*args, **kwargs):
        nonlocal proxy_started
        proxy_started = True
        raise AssertionError("proxy should not start when CA trust fails")

    monkeypatch.setattr("claude_tap.cli.ensure_ca", lambda: (ca_path, key_path))
    monkeypatch.setattr("claude_tap.cli._ensure_ca_trust_for_forward_proxy", lambda _args, _path: 7)
    monkeypatch.setattr("claude_tap.cli.ForwardProxyServer", fail_if_proxy_starts)

    code = await async_main(
        Namespace(
            output_dir=str(tmp_path / "traces"),
            live_viewer=False,
            live_port=0,
            host="127.0.0.1",
            proxy_mode="forward",
            trust_ca=True,
            port=0,
            client="agy",
            target="https://antigravity.goog",
            extra_allowed_paths=[],
            no_update_check=True,
            no_auto_update=True,
            no_launch=True,
            claude_args=[],
            max_traces=0,
            open_viewer=False,
        )
    )

    assert code == 7
    assert proxy_started is False
