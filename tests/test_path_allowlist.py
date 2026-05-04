"""Tests for proxy path allowlist filtering."""

import pytest

from claude_tap.proxy import _is_allowed_path


@pytest.mark.parametrize(
    "path",
    [
        "/v1/messages",
        "/v1/messages?stream=true",
        "/v1/complete",
        "/v1/responses",
        "/v1/chat/completions",
        "/v1/completions",
        "/v1/models",
        "/v1/models/claude-3",
        "/v1/embeddings",
        "/responses",
        "/chat/completions",
        "/completions",
        "/models",
        "/embeddings",
    ],
)
def test_allowed_paths(path: str):
    assert _is_allowed_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd",
        "/swagger/",
        "/swagger-ui.html",
        "/login.html",
        "/metrics",
        "/nacos/",
        "/nexus/",
        "/zabbix",
        "/vnc.html",
        "/",
        "/admin",
        "/wp-admin",
        "/.env",
        "/actuator/health",
        "/api/v1/hack",
    ],
)
def test_blocked_paths(path: str):
    assert _is_allowed_path(path) is False
