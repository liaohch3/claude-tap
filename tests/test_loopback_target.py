from __future__ import annotations

import pytest

from claude_tap.cli import _extend_no_proxy, _loopback_target_host


@pytest.mark.parametrize(
    ("target", "expected_host"),
    [
        ("http://127.0.0.1:23333/api/anthropic", "127.0.0.1"),
        ("http://127.0.0.2:23333", "127.0.0.2"),
        ("http://127.255.255.254:8080", "127.255.255.254"),
        ("http://localhost:8080", "localhost"),
        ("https://LOCALHOST:1/x", "localhost"),
        ("http://[::1]:9000", "::1"),
    ],
)
def test_loopback_target_host_detected(target: str, expected_host: str) -> None:
    assert _loopback_target_host(target) == expected_host


@pytest.mark.parametrize(
    "target",
    [
        "https://api.anthropic.com",
        "https://api.deepseek.com/anthropic",
        "http://10.0.0.5:23333",
        "http://8.8.8.8",
        None,
        "",
    ],
)
def test_non_loopback_targets_return_none(target: str | None) -> None:
    assert _loopback_target_host(target) is None


def test_extend_no_proxy_preserves_existing_entries() -> None:
    env = {"NO_PROXY": "example.com"}
    _extend_no_proxy(env, ("127.0.0.2",))
    entries = env["NO_PROXY"].split(",")
    assert "example.com" in entries
    assert "127.0.0.2" in entries
    # Mirrored into the lowercase variant for tools that read it.
    assert env["no_proxy"] == env["NO_PROXY"]


@pytest.mark.parametrize("key", ["NO_PROXY", "no_proxy"])
def test_extend_no_proxy_preserves_wildcard_sentinel(key: str) -> None:
    env = {key: "*"}
    _extend_no_proxy(env, ("127.0.0.2",))
    assert env["NO_PROXY"] == "*"
    assert env["no_proxy"] == "*"
