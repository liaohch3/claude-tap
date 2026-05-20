from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from claude_tap import parse_args
from claude_tap.certs import build_macos_trust_ca_command, build_macos_verify_ca_command
from claude_tap.cli import _trust_ca_for_current_user


def test_parse_args_accepts_tap_trust_ca() -> None:
    args = parse_args(["--tap-client", "agy", "--tap-trust-ca"])

    assert args.client == "agy"
    assert args.proxy_mode == "forward"
    assert args.trust_ca is True


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


def test_trust_ca_for_current_user_rejects_non_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("claude_tap.cli.sys.platform", "linux")

    code = _trust_ca_for_current_user(Path("/tmp/claude-tap-ca.pem"))

    assert code == 1


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
