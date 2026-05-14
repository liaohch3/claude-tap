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
        "/v1/files",
        "/responses",
        "/chat/completions",
        "/completions",
        "/models",
        "/embeddings",
        "/files",
        "/v1beta/models/gemini-2.5-pro:streamGenerateContent",
        "/v1beta/models/gemini-2.5-flash:generateContent",
        "/v1alpha/models/gemini-test:generateContent",
        "/search",
        "/fetch",
        "/usages",
        "/feedback",
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


@pytest.mark.parametrize(
    "path,extra_prefixes",
    [
        ("/custom/api/v1/messages", ("/custom/api",)),
        ("/custom/api/v1/completions", ("/custom/api",)),
        ("/my/api/endpoint", ("/my/api",)),
        ("/api/v2/models", ("/api/v2",)),
    ],
)
def test_extra_prefixes_allowed(path: str, extra_prefixes: tuple[str, ...]):
    assert _is_allowed_path(path, extra_prefixes) is True


@pytest.mark.parametrize(
    "path,extra_prefixes",
    [
        ("/etc/passwd", ("/custom/api",)),
        ("/swagger/", ("/custom/api",)),
        ("/api/v1/hack", ("/custom/api",)),
    ],
)
def test_extra_prefixes_blocked_when_not_matching(path: str, extra_prefixes: tuple[str, ...]):
    assert _is_allowed_path(path, extra_prefixes) is False
