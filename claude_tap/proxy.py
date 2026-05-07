"""Proxy handler – forward requests to upstream API and record traces."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import logging
import re
import time
import uuid
import zlib
from datetime import datetime, timezone

import aiohttp
from aiohttp import web
from yarl import URL

from claude_tap.sse import SSEReassembler
from claude_tap.trace import TraceWriter

log = logging.getLogger("claude-tap")

# ---------------------------------------------------------------------------
# Header helpers
# ---------------------------------------------------------------------------

HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)


def filter_headers(headers: dict[str, str], *, redact_keys: bool = False) -> dict[str, str]:
    """Filter hop-by-hop headers and optionally redact sensitive values."""
    out: dict[str, str] = {}
    for k, v in headers.items():
        if k.lower() in HOP_BY_HOP:
            continue
        if redact_keys and k.lower() in ("x-api-key", "authorization"):
            out[k] = v[:12] + "..." if len(v) > 12 else "***"
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# Path allowlist – only forward requests to known API endpoints.
# Scanners / crawlers hitting the proxy with paths like /etc/passwd, /swagger,
# /metrics etc. are rejected with 404 without forwarding or recording.
# ---------------------------------------------------------------------------

ALLOWED_PATH_PREFIXES: tuple[str, ...] = (
    # Anthropic API (Claude Code)
    "/v1/messages",
    "/v1/complete",
    # OpenAI API (Codex CLI)
    "/v1/responses",
    "/v1/chat/completions",
    "/v1/completions",
    "/v1/models",
    "/v1/embeddings",
    # OpenAI Responses API (after strip_path_prefix removes /v1)
    "/responses",
    "/chat/completions",
    "/completions",
    "/models",
    "/embeddings",
)


def _is_allowed_path(path: str) -> bool:
    """Check whether the request path matches a known API endpoint."""
    # Strip query string for matching
    clean = path.split("?", 1)[0].rstrip("/")
    return any(clean == prefix or clean.startswith(prefix + "/") for prefix in ALLOWED_PATH_PREFIXES)


_ANTHROPIC_METADATA_USER_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


def _is_deepseek_anthropic_target(target: str) -> bool:
    """Return True for DeepSeek's Anthropic-compatible API target."""
    try:
        url = URL(target)
    except ValueError:
        return False
    return url.host == "api.deepseek.com" and url.path.rstrip("/") == "/anthropic"


def _normalize_request_body_for_upstream(req_body: dict, target: str) -> dict:
    """Apply narrow upstream compatibility fixes without changing default Anthropic behavior."""
    if not _is_deepseek_anthropic_target(target):
        return req_body

    metadata = req_body.get("metadata")
    if not isinstance(metadata, dict):
        return req_body

    user_id = metadata.get("user_id")
    if not isinstance(user_id, str) or _ANTHROPIC_METADATA_USER_ID_PATTERN.fullmatch(user_id):
        return req_body

    normalized_body = dict(req_body)
    normalized_metadata = dict(metadata)
    digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:24]
    normalized_metadata["user_id"] = f"claude_tap_{digest}"
    normalized_body["metadata"] = normalized_metadata
    return normalized_body


# ---------------------------------------------------------------------------
# Proxy handler
# ---------------------------------------------------------------------------


async def proxy_handler(request: web.Request) -> web.StreamResponse:
    # Reject requests to unknown paths (scanner/crawler protection)
    if not _is_allowed_path(request.path):
        log.debug(f"Blocked non-API path: {request.method} {request.path}")
        return web.Response(status=404, text="Not Found")

    # Detect WebSocket upgrade and route to WS proxy.
    if request.headers.get("Upgrade", "").lower() == "websocket":
        if request.app["trace_ctx"].get("force_http"):
            log.info(f"Rejecting WebSocket upgrade on {request.path} (force_http); client will fallback to HTTP")
            return web.Response(status=426, text="Upgrade Required")
        from claude_tap.ws_proxy import _handle_websocket

        return await _handle_websocket(request)

    ctx: dict = request.app["trace_ctx"]
    target: str = ctx["target_url"]
    writer: TraceWriter = ctx["writer"]
    session: aiohttp.ClientSession = ctx["session"]

    # Strip path prefix (e.g. /v1) for codex client so that
    # /v1/responses -> target + /responses
    strip_prefix: str = ctx.get("strip_path_prefix", "")
    fwd_path = request.path_qs
    if strip_prefix and fwd_path.startswith(strip_prefix):
        fwd_path = fwd_path[len(strip_prefix) :] or "/"
    upstream_url = target.rstrip("/") + "/" + fwd_path.lstrip("/")

    # aiohttp auto-decompresses request bodies (gzip/deflate/zstd), so
    # request.read() returns plain bytes even when Content-Encoding is set.
    body = await request.read()

    fwd_headers = filter_headers(request.headers)
    fwd_headers.pop("Host", None)
    # Strip Content-Encoding since aiohttp already decompressed the body;
    # also remove stale Content-Length (aiohttp client will recompute it).
    req_content_encoding = request.headers.get("Content-Encoding", "").lower()
    if req_content_encoding in ("zstd", "gzip", "deflate", "br"):
        for key in list(fwd_headers.keys()):
            if key.lower() in ("content-encoding", "content-length"):
                del fwd_headers[key]

    req_id = f"req_{uuid.uuid4().hex[:12]}"
    t0 = time.monotonic()

    # Parse request body
    try:
        req_body = json.loads(body) if body else None
    except (json.JSONDecodeError, ValueError):
        req_body = body.decode("utf-8", errors="replace") if body else None

    upstream_body = body
    if isinstance(req_body, dict):
        normalized_req_body = _normalize_request_body_for_upstream(req_body, target)
        if normalized_req_body is not req_body:
            req_body = normalized_req_body
            upstream_body = json.dumps(req_body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            for key in list(fwd_headers.keys()):
                if key.lower() == "content-length":
                    del fwd_headers[key]

    is_streaming = False
    if isinstance(req_body, dict):
        is_streaming = req_body.get("stream", False)

    ctx["turn_counter"] = ctx.get("turn_counter", 0) + 1
    turn = ctx["turn_counter"]

    model = req_body.get("model", "") if isinstance(req_body, dict) else ""
    log_prefix = f"[Turn {turn}]"
    log.info(
        f"{log_prefix} → {request.method} {request.path} (model={model}, stream={is_streaming}, upstream={upstream_url})"
    )

    # Request identity encoding from upstream to avoid client-side zstd decode issues
    # and to simplify SSE/text reconstruction.
    fwd_headers["Accept-Encoding"] = "identity"

    try:
        upstream_resp = await session.request(
            method=request.method,
            url=upstream_url,
            headers=fwd_headers,
            data=upstream_body,
            timeout=aiohttp.ClientTimeout(total=600, sock_read=300),
        )
    except Exception as exc:
        log.error(
            f"{log_prefix} upstream error while requesting {upstream_url}: {exc}  "
            f"-- Check that the target ({target}) is reachable."
        )
        return web.Response(status=502, text=str(exc))

    if is_streaming and upstream_resp.status == 200:
        resp_body = await _handle_streaming(
            request,
            upstream_resp,
            req_id,
            turn,
            t0,
            req_body,
            writer,
            log_prefix,
            upstream_base_url=target,
        )
        return resp_body

    return await _handle_non_streaming(
        request,
        upstream_resp,
        req_id,
        turn,
        t0,
        req_body,
        writer,
        log_prefix,
        upstream_base_url=target,
    )


async def _handle_streaming(
    request: web.Request,
    upstream_resp: aiohttp.ClientResponse,
    req_id: str,
    turn: int,
    t0: float,
    req_body,
    writer: TraceWriter,
    log_prefix: str,
    upstream_base_url: str,
) -> web.StreamResponse:
    resp = web.StreamResponse(
        status=upstream_resp.status,
        headers={k: v for k, v in upstream_resp.headers.items() if k.lower() not in HOP_BY_HOP},
    )
    await resp.prepare(request)

    reassembler = SSEReassembler()

    try:
        async for chunk in upstream_resp.content.iter_any():
            await resp.write(chunk)
            reassembler.feed_bytes(chunk)
    except (ConnectionError, asyncio.CancelledError):
        pass

    try:
        await resp.write_eof()
    except (ConnectionError, ConnectionResetError, Exception):
        pass

    duration_ms = int((time.monotonic() - t0) * 1000)
    reconstructed = reassembler.reconstruct()

    usage = reconstructed.get("usage", {}) if reconstructed else {}
    in_tok = usage.get("input_tokens", 0)
    out_tok = usage.get("output_tokens", 0)
    cache_read = usage.get("cache_read_input_tokens", 0)
    cache_create = usage.get("cache_creation_input_tokens", 0)
    log.info(
        f"{log_prefix} ← 200 stream done ({duration_ms}ms, "
        f"in={in_tok} out={out_tok} cache_read={cache_read} cache_create={cache_create})"
    )

    record = _build_record(
        req_id,
        turn,
        duration_ms,
        request.method,
        request.path_qs,
        request.headers,
        req_body,
        upstream_resp.status,
        upstream_resp.headers,
        reconstructed,
        sse_events=reassembler.events,
        upstream_base_url=upstream_base_url,
    )
    await writer.write(record)

    return resp


async def _handle_non_streaming(
    request: web.Request,
    upstream_resp: aiohttp.ClientResponse,
    req_id: str,
    turn: int,
    t0: float,
    req_body,
    writer: TraceWriter,
    log_prefix: str,
    upstream_base_url: str,
) -> web.Response:
    resp_bytes = await upstream_resp.read()
    duration_ms = int((time.monotonic() - t0) * 1000)

    # Decompress for JSON parsing (raw bytes are forwarded as-is to client)
    content_encoding = upstream_resp.headers.get("Content-Encoding", "").lower()
    decode_bytes = resp_bytes
    if resp_bytes and content_encoding in ("gzip", "deflate"):
        try:
            if content_encoding == "gzip":
                decode_bytes = gzip.decompress(resp_bytes)
            else:
                decode_bytes = zlib.decompress(resp_bytes)
        except Exception:
            pass

    try:
        resp_body = json.loads(decode_bytes) if decode_bytes else None
    except (json.JSONDecodeError, ValueError):
        resp_body = decode_bytes.decode("utf-8", errors="replace") if decode_bytes else None

    log.info(f"{log_prefix} ← {upstream_resp.status} ({duration_ms}ms, {len(resp_bytes)} bytes)")

    record = _build_record(
        req_id,
        turn,
        duration_ms,
        request.method,
        request.path_qs,
        request.headers,
        req_body,
        upstream_resp.status,
        upstream_resp.headers,
        resp_body,
        upstream_base_url=upstream_base_url,
    )
    await writer.write(record)

    return web.Response(
        status=upstream_resp.status,
        headers={k: v for k, v in upstream_resp.headers.items() if k.lower() not in HOP_BY_HOP},
        body=resp_bytes,
    )


def _build_record(
    req_id: str,
    turn: int,
    duration_ms: int,
    method: str,
    path_qs: str,
    req_headers: dict,
    req_body: dict | None,
    status: int,
    resp_headers: dict,
    resp_body: dict | None,
    sse_events: list[dict] | None = None,
    upstream_base_url: str | None = None,
) -> dict:
    """Build a trace record for a single API call."""
    record: dict = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": req_id,
        "turn": turn,
        "duration_ms": duration_ms,
        "request": {
            "method": method,
            "path": path_qs,
            "headers": filter_headers(req_headers, redact_keys=True),
            "body": req_body,
        },
        "response": {
            "status": status,
            "headers": filter_headers(resp_headers),
            "body": resp_body,
        },
    }
    if sse_events:
        record["response"]["sse_events"] = sse_events
    if upstream_base_url:
        record["upstream_base_url"] = upstream_base_url
    return record
