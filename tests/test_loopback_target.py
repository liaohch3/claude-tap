from __future__ import annotations

import pytest

from claude_tap.cli import _is_loopback_target


@pytest.mark.parametrize(
    "target",
    [
        "http://127.0.0.1:23333/api/anthropic",
        "http://localhost:8080",
        "https://LOCALHOST:1/x",
        "http://[::1]:9000",
    ],
)
def test_loopback_targets_detected(target: str) -> None:
    assert _is_loopback_target(target) is True


@pytest.mark.parametrize(
    "target",
    [
        "https://api.anthropic.com",
        "https://api.deepseek.com/anthropic",
        "http://10.0.0.5:23333",
        None,
        "",
    ],
)
def test_non_loopback_targets_rejected(target: str | None) -> None:
    assert _is_loopback_target(target) is False
