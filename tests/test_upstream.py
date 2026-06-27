"""Tests for upstream URL construction and connection diagnostics."""

from __future__ import annotations

import ssl

import pytest

from claude_tap.upstream import (
    KNOWN_UPSTREAM_ENDPOINT_PATHS,
    build_upstream_url,
    format_upstream_error,
    is_ssl_certificate_error,
)


@pytest.mark.parametrize("endpoint", KNOWN_UPSTREAM_ENDPOINT_PATHS)
def test_build_upstream_url_avoids_duplicate_known_endpoint(endpoint: str) -> None:
    url = build_upstream_url(f"https://gateway.example/proxy{endpoint}", endpoint)

    assert url == f"https://gateway.example/proxy{endpoint}"


@pytest.mark.parametrize(
    ("versioned_endpoint", "short_endpoint"),
    [
        ("/v1/chat/completions", "/chat/completions"),
        ("/v1/messages", "/messages"),
        ("/v1/responses", "/responses"),
        ("/v1/completions", "/completions"),
    ],
)
def test_build_upstream_url_allows_short_request_path_against_versioned_endpoint(
    versioned_endpoint: str, short_endpoint: str
) -> None:
    url = build_upstream_url(f"https://gateway.example/proxy{versioned_endpoint}", f"{short_endpoint}?stream=true")

    assert url == f"https://gateway.example/proxy{versioned_endpoint}?stream=true"


def test_build_upstream_url_avoids_duplicate_messages_endpoint() -> None:
    url = build_upstream_url("http://example.com/proxy/v1/messages", "/v1/messages")

    assert url == "http://example.com/proxy/v1/messages"


def test_build_upstream_url_avoids_duplicate_messages_subpath_and_query() -> None:
    url = build_upstream_url("http://example.com/proxy/v1/messages", "/v1/messages/count_tokens?beta=true")

    assert url == "http://example.com/proxy/v1/messages/count_tokens?beta=true"


def test_build_upstream_url_allows_short_endpoint_path_against_versioned_target() -> None:
    url = build_upstream_url("http://example.com/proxy/v1/messages", "/messages?stream=true")

    assert url == "http://example.com/proxy/v1/messages?stream=true"


def test_build_upstream_url_keeps_normal_base_url_join() -> None:
    url = build_upstream_url("https://api.moonshot.ai/v1", "/chat/completions")

    assert url == "https://api.moonshot.ai/v1/chat/completions"


def test_build_upstream_url_preserves_endpoint_base_for_openai_style_target() -> None:
    url = build_upstream_url("https://gateway.example/v1/chat/completions", "/chat/completions")

    assert url == "https://gateway.example/v1/chat/completions"


def test_format_upstream_error_adds_ssl_certificate_diagnostics() -> None:
    exc = ssl.SSLCertVerificationError("certificate verify failed: unable to get local issuer certificate")

    text = format_upstream_error(
        exc,
        target_url="https://user:secret@gateway.example/v1/messages",
        upstream_url="https://user:secret@gateway.example/v1/messages",
    )

    assert is_ssl_certificate_error(exc)
    assert "SSL_CERT_FILE" in text
    assert "provider base URL" in text
    assert "user:secret" not in text
    assert "***@gateway.example" in text


def test_format_upstream_error_keeps_non_ssl_error_short() -> None:
    exc = ConnectionError("connection refused")

    assert format_upstream_error(exc, target_url="http://example.test", upstream_url="http://example.test") == (
        "connection refused"
    )
