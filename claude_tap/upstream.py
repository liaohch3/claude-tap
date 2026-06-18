"""Helpers for upstream URL construction and connection diagnostics."""

from __future__ import annotations

import ssl
from urllib.parse import urlsplit, urlunsplit

KNOWN_UPSTREAM_ENDPOINT_PATHS = (
    "/v1/chat/completions",
    "/chat/completions",
    "/v1/messages",
    "/messages",
    "/v1/responses",
    "/responses",
    "/v1/completions",
    "/completions",
)


def build_upstream_url(target_url: str, forward_path: str) -> str:
    """Join a configured upstream target with a forwarded request path.

    Some users pass a complete request endpoint such as
    ``https://gateway.example/v1/messages`` to ``--tap-target``. Avoid turning
    a client request for ``/v1/messages`` into ``/v1/messages/v1/messages``.
    """

    target = urlsplit(target_url)
    request_path, request_query = _split_forward_path(forward_path)
    path = _join_without_duplicate_endpoint(target.path, request_path)
    query = request_query or target.query
    return urlunsplit((target.scheme, target.netloc, path, query, target.fragment))


def format_upstream_error(exc: BaseException, *, target_url: str, upstream_url: str) -> str:
    """Return a user-facing upstream connection error with actionable context."""

    base = str(exc) or exc.__class__.__name__
    if not is_ssl_certificate_error(exc):
        return base

    safe_target = _redact_url_userinfo(target_url)
    safe_upstream = _redact_url_userinfo(upstream_url)
    return (
        f"{base}\n\n"
        "claude-tap could not verify the upstream TLS certificate. This commonly happens when "
        "a corporate proxy, private gateway, or local network tool presents a certificate that "
        "Python/aiohttp does not trust.\n\n"
        "Set SSL_CERT_FILE to a CA bundle that trusts that proxy or gateway, then retry. On "
        "macOS with the system CA bundle this is often:\n"
        "  SSL_CERT_FILE=/etc/ssl/cert.pem claude-tap --tap-live --tap-target <upstream-base-url>\n\n"
        "Also make sure --tap-target is the provider base URL, not a full request endpoint such "
        "as /v1/messages.\n"
        f"Configured target: {safe_target}\n"
        f"Upstream URL: {safe_upstream}"
    )


def is_ssl_certificate_error(exc: BaseException) -> bool:
    """Return whether an exception chain contains a TLS certificate verify failure."""

    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ssl.SSLCertVerificationError):
            return True
        cert_error = getattr(current, "certificate_error", None)
        if isinstance(cert_error, ssl.SSLCertVerificationError):
            return True
        current = current.__cause__ or current.__context__

    text = f"{exc.__class__.__name__}: {exc}"
    return (
        "SSLCertVerificationError" in text or "CERTIFICATE_VERIFY_FAILED" in text or "certificate verify failed" in text
    )


def _split_forward_path(forward_path: str) -> tuple[str, str]:
    path, sep, query = forward_path.partition("?")
    if not path:
        path = "/"
    elif not path.startswith("/"):
        path = f"/{path}"
    return path, query if sep else ""


def _join_without_duplicate_endpoint(target_path: str, request_path: str) -> str:
    clean_target = target_path.rstrip("/")
    clean_request = request_path if request_path.startswith("/") else f"/{request_path}"

    for endpoint in KNOWN_UPSTREAM_ENDPOINT_PATHS:
        if not clean_target.endswith(endpoint):
            continue
        if clean_request == endpoint or clean_request.startswith(f"{endpoint}/"):
            base = clean_target[: -len(endpoint)].rstrip("/")
            return f"{base}{clean_request}" or "/"
        short_endpoint = _without_version_prefix(endpoint)
        if short_endpoint and (clean_request == short_endpoint or clean_request.startswith(f"{short_endpoint}/")):
            remainder = clean_request[len(short_endpoint) :]
            return f"{clean_target}{remainder}" or "/"

    if clean_request == "/":
        return clean_target or "/"
    return f"{clean_target}/{clean_request.lstrip('/')}" if clean_target else clean_request


def _without_version_prefix(endpoint: str) -> str | None:
    if endpoint.startswith("/v1/"):
        return endpoint[3:]
    return None


def _redact_url_userinfo(url: str) -> str:
    parsed = urlsplit(url)
    if "@" not in parsed.netloc:
        return url
    host_part = parsed.netloc.rsplit("@", 1)[1]
    return urlunsplit((parsed.scheme, f"***@{host_part}", parsed.path, parsed.query, parsed.fragment))
