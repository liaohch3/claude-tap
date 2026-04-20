"""Proxy handler – forward requests to upstream API and record traces."""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import time
import uuid
import zlib
from datetime import datetime, timezone

import aiohttp
from aiohttp import web
from aiohttp.helpers import get_env_proxy_for_url
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


# ---------------------------------------------------------------------------
# Proxy handler
# ---------------------------------------------------------------------------


async def proxy_handler(request: web.Request) -> web.StreamResponse:
    # Reject requests to unknown paths (scanner/crawler protection)
    if not _is_allowed_path(request.path):
        log.debug(f"Blocked non-API path: {request.method} {request.path}")
        return web.Response(status=404, text="Not Found")

    # Detect WebSocket upgrade and route to WS proxy
    if request.headers.get("Upgrade", "").lower() == "websocket":
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
            data=body,
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


# ---------------------------------------------------------------------------
# WebSocket proxy
# ---------------------------------------------------------------------------

# Headers managed by the WebSocket handshake — must not be forwarded to upstream.
_WS_HANDSHAKE_HEADERS = frozenset(
    {
        "sec-websocket-key",
        "sec-websocket-version",
        "sec-websocket-extensions",
        "sec-websocket-protocol",
        "sec-websocket-accept",
    }
)


def _get_ws_proxy_settings(ws_url: str) -> tuple[URL, aiohttp.BasicAuth | None] | None:
    """Resolve HTTP proxy and auth from env for a WebSocket URL.

    aiohttp's ``ws_connect`` does not check ``trust_env`` to auto-resolve
    proxy settings from environment variables (unlike ``_request``).
    ``get_env_proxy_for_url`` also doesn't recognise the ``wss://``/``ws://``
    schemes.  Work around both by converting the scheme to its HTTP equivalent
    (``wss`` → ``https``, ``ws`` → ``http``) for the lookup.
    """
    if ws_url.startswith("wss://"):
        lookup_url = URL("https://" + ws_url[6:])
    elif ws_url.startswith("ws://"):
        lookup_url = URL("http://" + ws_url[5:])
    else:
        return None

    try:
        return get_env_proxy_for_url(lookup_url)
    except LookupError:
        return None


async def _handle_websocket(request: web.Request) -> web.StreamResponse:
    """Proxy a WebSocket connection to the upstream, recording all messages."""
    ctx: dict = request.app["trace_ctx"]
    target: str = ctx["target_url"]
    writer: TraceWriter = ctx["writer"]
    session: aiohttp.ClientSession = ctx["session"]

    strip_prefix: str = ctx.get("strip_path_prefix", "")
    fwd_path = request.path_qs
    if strip_prefix and fwd_path.startswith(strip_prefix):
        fwd_path = fwd_path[len(strip_prefix) :] or "/"
    upstream_url = target.rstrip("/") + "/" + fwd_path.lstrip("/")

    # Convert HTTP scheme to WebSocket scheme for upstream
    if upstream_url.startswith("https://"):
        upstream_ws_url = "wss://" + upstream_url[8:]
    elif upstream_url.startswith("http://"):
        upstream_ws_url = "ws://" + upstream_url[7:]
    else:
        upstream_ws_url = upstream_url

    # Forward auth headers, strip hop-by-hop and WS handshake headers
    fwd_headers = filter_headers(request.headers)
    fwd_headers.pop("Host", None)
    for h in list(fwd_headers.keys()):
        if h.lower() in _WS_HANDSHAKE_HEADERS:
            del fwd_headers[h]

    # Forward WebSocket subprotocol if present
    protocols: tuple[str, ...] = ()
    ws_protocol = request.headers.get("Sec-WebSocket-Protocol")
    if ws_protocol:
        protocols = tuple(p.strip() for p in ws_protocol.split(","))

    req_id = f"req_{uuid.uuid4().hex[:12]}"
    t0 = time.monotonic()
    ctx["turn_counter"] = ctx.get("turn_counter", 0) + 1
    turn = ctx["turn_counter"]
    log_prefix = f"[Turn {turn}]"

    # Resolve proxy from env — aiohttp ws_connect ignores trust_env
    proxy_settings = _get_ws_proxy_settings(upstream_ws_url) if session.trust_env else None
    ws_connect_kwargs: dict[str, object] = {}
    if proxy_settings:
        proxy_url, proxy_auth = proxy_settings
        ws_connect_kwargs["proxy"] = proxy_url
        if proxy_auth is not None:
            ws_connect_kwargs["proxy_auth"] = proxy_auth
        log.info(f"{log_prefix} → WS UPGRADE {request.path_qs} (upstream={upstream_ws_url}, via proxy {proxy_url})")
    else:
        log.info(f"{log_prefix} → WS UPGRADE {request.path_qs} (upstream={upstream_ws_url})")

    # Connect to upstream first — if it fails, return HTTP 502 before upgrading
    try:
        upstream_ws = await session.ws_connect(
            upstream_ws_url,
            headers=fwd_headers,
            protocols=protocols,
            **ws_connect_kwargs,
        )
    except Exception as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        log.error(f"{log_prefix} upstream WS connect to {upstream_ws_url} failed: {exc}")
        record = _build_ws_record(
            req_id=req_id,
            turn=turn,
            duration_ms=duration_ms,
            path_qs=request.path_qs,
            req_headers=request.headers,
            client_messages=[],
            server_messages=[],
            upstream_base_url=target,
            error=str(exc),
        )
        await writer.write(record)
        return web.Response(status=502, text=str(exc))

    # Upstream connected — accept client WebSocket upgrade
    client_ws = web.WebSocketResponse(protocols=protocols)
    await client_ws.prepare(request)

    client_messages: list[str] = []
    server_messages: list[str] = []

    async def _relay_client_to_upstream():
        try:
            async for msg in client_ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    client_messages.append(msg.data)
                    await upstream_ws.send_str(msg.data)
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    await upstream_ws.send_bytes(msg.data)
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.CLOSED,
                ):
                    break
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    break
        except (ConnectionError, asyncio.CancelledError):
            pass

    async def _relay_upstream_to_client():
        try:
            async for msg in upstream_ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    server_messages.append(msg.data)
                    await client_ws.send_str(msg.data)
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    await client_ws.send_bytes(msg.data)
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.CLOSED,
                ):
                    break
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    break
        except (ConnectionError, asyncio.CancelledError):
            pass

    # Run bidirectional relay — stop when either side closes
    tasks = [
        asyncio.create_task(_relay_client_to_upstream()),
        asyncio.create_task(_relay_upstream_to_client()),
    ]
    _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    if not upstream_ws.closed:
        await upstream_ws.close()
    if not client_ws.closed:
        await client_ws.close()

    duration_ms = int((time.monotonic() - t0) * 1000)

    record = _build_ws_record(
        req_id=req_id,
        turn=turn,
        duration_ms=duration_ms,
        path_qs=request.path_qs,
        req_headers=request.headers,
        client_messages=client_messages,
        server_messages=server_messages,
        upstream_base_url=target,
    )
    await writer.write(record)

    log.info(
        f"{log_prefix} ← WS closed ({duration_ms}ms, "
        f"{len(client_messages)} client→upstream, "
        f"{len(server_messages)} upstream→client)"
    )

    return client_ws


def _build_ws_record(
    req_id: str,
    turn: int,
    duration_ms: int,
    path_qs: str,
    req_headers: dict,
    client_messages: list[str],
    server_messages: list[str],
    upstream_base_url: str,
    error: str | None = None,
) -> dict:
    """Build a trace record for a WebSocket session."""
    req_body = _reconstruct_ws_request_body(client_messages)

    # Parse server messages into structured events
    ws_events: list[dict] = []
    for msg in server_messages:
        try:
            parsed = json.loads(msg)
            ws_events.append(parsed)
        except (json.JSONDecodeError, ValueError):
            ws_events.append({"raw": msg})

    resp_body = _reconstruct_ws_response_body(ws_events)

    record: dict = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": req_id,
        "turn": turn,
        "duration_ms": duration_ms,
        "transport": "websocket",
        "request": {
            "method": "WEBSOCKET",
            "path": path_qs,
            "headers": filter_headers(req_headers, redact_keys=True),
            "body": req_body,
        },
        "response": {
            "status": 101 if not error else 502,
            "headers": {},
            "body": resp_body,
        },
    }
    if ws_events:
        record["response"]["ws_events"] = ws_events
    if error:
        record["response"]["error"] = error
    if upstream_base_url:
        record["upstream_base_url"] = upstream_base_url
    return record


def _reconstruct_ws_request_body(client_messages: list[str]) -> dict | None:
    """Merge client WebSocket messages into the most complete request body."""
    merged: dict | None = None
    for msg in client_messages:
        try:
            parsed = json.loads(msg)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(parsed, dict):
            continue
        if merged is None:
            merged = parsed.copy()
            continue
        for key, value in parsed.items():
            if key in ("input", "tools"):
                if value:
                    merged[key] = value
                else:
                    merged.setdefault(key, value)
                continue
            if value not in (None, "", [], {}):
                merged[key] = value
            else:
                merged.setdefault(key, value)
    return merged


def _reconstruct_ws_response_body(ws_events: list[dict]) -> dict | None:
    """Build a best-effort response body from WS events.

    Recent Codex versions may emit multiple response.completed events and keep
    the actual assistant text inside response.output_item.done rather than the
    terminal response payload. Reconstruct a richer body for traces/viewer use.
    """
    merged: dict | None = None
    output_items: dict[int, dict] = {}

    for event in ws_events:
        if not isinstance(event, dict):
            continue

        event_type = event.get("type")
        payload = event.get("response", event)
        if isinstance(payload, dict) and event_type in (
            "response.created",
            "response.in_progress",
            "response.completed",
            "response.done",
        ):
            if merged is None:
                merged = payload.copy()
            else:
                for key, value in payload.items():
                    if key == "output":
                        if value:
                            merged[key] = value
                        else:
                            merged.setdefault(key, value)
                        continue
                    if key == "usage":
                        if value:
                            merged[key] = value
                        else:
                            merged.setdefault(key, value)
                        continue
                    if value not in (None, "", [], {}):
                        merged[key] = value
                    else:
                        merged.setdefault(key, value)

        if event_type == "response.output_item.done":
            item = event.get("item")
            output_index = event.get("output_index")
            if isinstance(item, dict) and isinstance(output_index, int):
                output_items[output_index] = item

    if output_items:
        ordered_output = [output_items[idx] for idx in sorted(output_items)]
        if merged is None:
            merged = {"output": ordered_output}
        elif not merged.get("output"):
            merged["output"] = ordered_output

    return merged


def reconstruct_ws_response_body(ws_events: list[dict]) -> dict | None:
    """Public wrapper for websocket response-body reconstruction.

    Forward and reverse proxy code paths both need identical reconstruction
    behavior so viewer output stays consistent across transport modes.
    """
    return _reconstruct_ws_response_body(ws_events)


def reconstruct_ws_request_body(client_messages: list[str]) -> dict | None:
    """Public wrapper for websocket request-body reconstruction."""
    return _reconstruct_ws_request_body(client_messages)
