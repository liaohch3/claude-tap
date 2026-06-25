"""Proxy handler – forward requests to upstream API and record traces."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import logging
import re
import struct
import time
import uuid
import zlib
from datetime import datetime, timezone

import aiohttp
from aiohttp import web
from yarl import URL

from claude_tap.bedrock import attach_bedrock_errors, bedrock_model_from_path, is_bedrock_eventstream_path
from claude_tap.sse import SSEReassembler
from claude_tap.trace import TraceWriter
from claude_tap.upstream import build_upstream_url, format_upstream_error
from claude_tap.usage import normalize_usage
from claude_tap.viewer import _decode_bedrock_eventstream_events

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

SENSITIVE_HEADER_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "set-cookie2",
        "x-api-key",
        "x-amz-security-token",
        # Qoder/Cosy runtime headers can carry account, machine, or token-derived
        # identifiers and must not be persisted in trace evidence.
        "cosy-key",
        "cosy-machinetoken",
        "cosy-machine-token",
        "cosy-machineid",
        "cosy-machine-id",
        "cosy-machinetype",
        "cosy-machine-type",
        "cosy-user",
    }
)
PREFIX_REDACTED_HEADER_KEYS = frozenset({"authorization", "x-api-key"})


def filter_headers(headers: dict[str, str], *, redact_keys: bool = False) -> dict[str, str]:
    """Filter hop-by-hop headers and optionally redact sensitive values."""
    out: dict[str, str] = {}
    for k, v in headers.items():
        key = k.lower()
        if key in HOP_BY_HOP:
            continue
        if redact_keys and key in SENSITIVE_HEADER_KEYS:
            out[k] = v[:12] + "..." if key in PREFIX_REDACTED_HEADER_KEYS and len(v) > 12 else "***"
        else:
            out[k] = v
    return out


def _parse_request_body_for_trace(body: bytes) -> object:
    """Parse a request body for trace storage without mutating upstream bytes."""
    if not body:
        return None

    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return body.decode("utf-8", errors="replace")

    if isinstance(parsed, str):
        try:
            inner = json.loads(parsed)
        except (json.JSONDecodeError, ValueError):
            return parsed
        if isinstance(inner, dict):
            return inner

    return parsed


# ---------------------------------------------------------------------------
# Path allowlist – only forward requests to known API endpoints.
# Scanners / crawlers hitting the proxy with paths like /etc/passwd, /swagger,
# /metrics etc. are rejected with 404 without forwarding or recording.
# ---------------------------------------------------------------------------

ALLOWED_PATH_PREFIXES: tuple[str, ...] = (
    # Anthropic API (Claude Code)
    "/v1/messages",
    "/v1/complete",
    # AWS Bedrock API (Claude Code via Bedrock)
    "/model",
    # OpenAI API (Codex CLI)
    "/v1/responses",
    "/v1/chat/completions",
    "/v1/completions",
    "/v1/models",
    "/v1/embeddings",
    "/v1/files",
    # OpenAI Responses API (after strip_path_prefix removes /v1)
    "/responses",
    "/chat/completions",
    "/completions",
    "/models",
    "/embeddings",
    "/files",
    # Gemini API
    "/v1beta/models",
    "/v1alpha/models",
    # Google Code Assist / Antigravity internal API
    "/v1internal",
    # Kimi Code auxiliary APIs (when users proxy Kimi Code services explicitly)
    "/search",
    "/fetch",
    "/usages",
    "/feedback",
)


def _is_allowed_path(path: str, extra_prefixes: tuple[str, ...] = ()) -> bool:
    """Check whether the request path matches a known API endpoint."""
    clean = path.split("?", 1)[0].rstrip("/")
    prefixes = ALLOWED_PATH_PREFIXES + extra_prefixes
    return any(
        clean == prefix or clean.startswith(prefix + "/") or clean.startswith(prefix + ":") for prefix in prefixes
    )


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


def is_capture_only_request(path: str, req_body: object) -> bool:
    """Return whether capture-only mode should short-circuit this request.

    Forward proxy clients may make unrelated HTTPS calls during startup. Prompt
    export mode should only synthesize model API responses; everything else can
    continue upstream and be filtered by the normal trace-skip rules.
    """

    clean_path = path.split("?", 1)[0]
    if clean_path.startswith(("/v1/embeddings", "/embeddings", "/v1/files", "/files")):
        return False
    if clean_path.startswith(
        (
            "/v1/messages",
            "/v1/complete",
            "/model/",
            "/v1/responses",
            "/responses",
            "/v1/chat/completions",
            "/chat/completions",
            "/v1/completions",
            "/completions",
            "/v1/models",
            "/models",
            "/v1beta/models",
            "/v1alpha/models",
        )
    ):
        return True
    if clean_path.startswith(("/v1internal:", "/v1internal/")):
        return "generatecontent" in clean_path.lower()
    if isinstance(req_body, dict) and isinstance(req_body.get("request"), dict):
        return is_capture_only_request(path, req_body["request"])
    return isinstance(req_body, dict) and any(
        key in req_body for key in ("system", "messages", "instructions", "input", "contents", "system_instruction")
    )


def is_capture_only_streaming_request(path: str, req_body: object) -> bool:
    """Return whether a captured request expects a streaming response by path or body."""

    if is_bedrock_eventstream_path(path):
        return True
    if "streamGenerateContent" in path:
        return True
    return isinstance(req_body, dict) and bool(req_body.get("stream", False))


def capture_only_content_type(path: str, is_streaming: bool) -> str:
    if is_bedrock_eventstream_path(path):
        return "application/vnd.amazon.eventstream"
    if is_streaming:
        return "text/event-stream"
    return "application/json"


def capture_only_response(path: str, req_body: object) -> dict:
    """Return a protocol-shaped success response without contacting upstream."""
    model = req_body.get("model", "claude-tap-capture") if isinstance(req_body, dict) else "claude-tap-capture"
    clean_path = path.split("?", 1)[0]
    if clean_path.startswith("/model/") and clean_path.rstrip("/").endswith("/converse"):
        return {
            "output": {"message": {"role": "assistant", "content": [{"text": "captured"}]}},
            "stopReason": "end_turn",
            "usage": {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0},
        }
    if clean_path.startswith("/v1/complete"):
        return _capture_only_anthropic_completion_response(model)
    if clean_path.startswith(("/v1/messages", "/model/")):
        return {
            "id": "msg_claude_tap_capture",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": "captured"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }
    if clean_path.startswith(("/v1internal:", "/v1internal/")):
        return _capture_only_gemini_generation_response()
    if clean_path in {"/v1/models", "/models"}:
        return {"object": "list", "data": [{"id": str(model), "object": "model"}]}
    if clean_path.startswith(("/v1/models/", "/models/")):
        model_id = clean_path.rsplit("/", 1)[-1] or str(model)
        return {"id": model_id, "object": "model", "created": 0, "owned_by": "claude-tap"}
    if clean_path.startswith(("/v1beta/models", "/v1alpha/models")):
        if ":" not in clean_path.rsplit("/", 1)[-1]:
            return _capture_only_gemini_model_response(clean_path, model)
        return _capture_only_gemini_generation_response()
    if clean_path.startswith(("/v1/completions", "/completions")):
        return {
            "id": "cmpl_claude_tap_capture",
            "object": "text_completion",
            "created": 0,
            "model": model,
            "choices": [{"index": 0, "text": "captured", "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
    if "chat/completions" in clean_path:
        return {
            "id": "chatcmpl_claude_tap_capture",
            "object": "chat.completion",
            "created": 0,
            "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "captured"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
    return {
        "id": "resp_claude_tap_capture",
        "object": "response",
        "created_at": 0,
        "model": model,
        "status": "completed",
        "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "captured"}]}],
        "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
    }


def _capture_only_anthropic_completion_response(model: object) -> dict:
    return {
        "id": "compl_claude_tap_capture",
        "type": "completion",
        "model": model,
        "completion": "captured",
        "stop_reason": "stop_sequence",
    }


def _capture_only_gemini_generation_response() -> dict:
    return {
        "candidates": [
            {
                "content": {"role": "model", "parts": [{"text": "captured"}]},
                "finishReason": "STOP",
                "index": 0,
            }
        ],
        "usageMetadata": {"promptTokenCount": 0, "candidatesTokenCount": 0, "totalTokenCount": 0},
    }


def _capture_only_gemini_model_response(clean_path: str, model: object) -> dict:
    if clean_path in {"/v1beta/models", "/v1alpha/models"}:
        model_name = f"models/{model}"
        return {"models": [_capture_only_gemini_model(model_name)]}

    model_id = clean_path.rsplit("/", 1)[-1] or str(model)
    model_name = model_id if model_id.startswith("models/") else f"models/{model_id}"
    return _capture_only_gemini_model(model_name)


def _capture_only_gemini_model(model_name: str) -> dict:
    model_id = model_name.rsplit("/", 1)[-1]
    return {
        "name": model_name,
        "version": model_id,
        "displayName": model_id,
        "supportedGenerationMethods": ["generateContent", "streamGenerateContent"],
    }


def capture_only_stream_bytes(path: str, req_body: object) -> bytes:
    """Return a small provider-shaped SSE response for streaming capture-only requests."""

    resp_body = capture_only_response(path, req_body)
    clean_path = path.split("?", 1)[0]
    if is_bedrock_eventstream_path(path):
        return _capture_only_bedrock_eventstream_bytes(path)
    if clean_path.startswith("/v1/complete"):
        chunk = {**resp_body, "stop_reason": None}
        done = {**resp_body, "completion": "", "stop_reason": "stop_sequence"}
        return (
            f"data: {json.dumps(chunk, separators=(',', ':'))}\n\ndata: {json.dumps(done, separators=(',', ':'))}\n\n"
        ).encode("utf-8")
    if clean_path.startswith("/v1/messages"):
        events = [
            ("message_start", {"type": "message_start", "message": resp_body}),
            (
                "content_block_start",
                {"type": "content_block_start", "index": 0, "content_block": resp_body["content"][0]},
            ),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}),
            ("message_stop", {"type": "message_stop"}),
        ]
        return b"".join(
            f"event: {event}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n".encode("utf-8")
            for event, payload in events
        )
    if clean_path.startswith(("/v1/completions", "/completions")):
        chunk = {
            "id": resp_body["id"],
            "object": "text_completion",
            "created": 0,
            "model": resp_body["model"],
            "choices": [{"index": 0, "text": "captured", "finish_reason": None}],
        }
        done = {
            "id": resp_body["id"],
            "object": "text_completion",
            "created": 0,
            "model": resp_body["model"],
            "choices": [{"index": 0, "text": "", "finish_reason": "stop"}],
        }
        return (
            f"data: {json.dumps(chunk, separators=(',', ':'))}\n\n"
            f"data: {json.dumps(done, separators=(',', ':'))}\n\n"
            "data: [DONE]\n\n"
        ).encode("utf-8")
    if "chat/completions" in clean_path:
        chunk = {
            "id": resp_body["id"],
            "object": "chat.completion.chunk",
            "created": 0,
            "model": resp_body["model"],
            "choices": [{"index": 0, "delta": {"content": "captured"}, "finish_reason": None}],
        }
        done = {
            "id": resp_body["id"],
            "object": "chat.completion.chunk",
            "created": 0,
            "model": resp_body["model"],
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        return (
            f"data: {json.dumps(chunk, separators=(',', ':'))}\n\n"
            f"data: {json.dumps(done, separators=(',', ':'))}\n\n"
            "data: [DONE]\n\n"
        ).encode("utf-8")
    if clean_path.startswith(("/v1beta/models", "/v1alpha/models", "/v1internal:", "/v1internal/")):
        return f"data: {json.dumps(resp_body, separators=(',', ':'))}\n\n".encode("utf-8")

    created = {"type": "response.created", "response": {**resp_body, "status": "in_progress"}}
    completed = {"type": "response.completed", "response": {**resp_body, "status": "completed"}}
    return (
        f"data: {json.dumps(created, separators=(',', ':'))}\n\n"
        f"data: {json.dumps(completed, separators=(',', ':'))}\n\n"
        "data: [DONE]\n\n"
    ).encode("utf-8")


def _capture_only_bedrock_eventstream_bytes(path: str) -> bytes:
    model = bedrock_model_from_path(path) or "claude-tap-capture"
    if path.split("?", 1)[0].rstrip("/").endswith("/converse-stream"):
        events = [
            {"messageStart": {"role": "assistant"}},
            {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "captured"}}},
            {"contentBlockStop": {"contentBlockIndex": 0}},
            {"messageStop": {"stopReason": "end_turn"}},
            {"metadata": {"usage": {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0}}},
        ]
        return b"".join(_capture_only_bedrock_frame(event, next(iter(event))) for event in events)
    else:
        events = [
            {
                "type": "message_start",
                "message": {
                    "id": "msg_claude_tap_capture",
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "content": [],
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            },
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "captured"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 0}},
            {"type": "message_stop"},
        ]
    return b"".join(_capture_only_bedrock_frame(event) for event in events)


def _capture_only_bedrock_frame(payload: dict, event_type: str = "chunk") -> bytes:
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = b"".join(
        _bedrock_eventstream_header(name, value)
        for name, value in (
            (":message-type", "event"),
            (":event-type", event_type),
            (":content-type", "application/json"),
        )
    )
    prelude = struct.pack("!II", 16 + len(headers) + len(payload_bytes), len(headers))
    prelude_crc = struct.pack("!I", zlib.crc32(prelude) & 0xFFFFFFFF)
    message = prelude + prelude_crc + headers + payload_bytes
    return message + struct.pack("!I", zlib.crc32(message) & 0xFFFFFFFF)


def _bedrock_eventstream_header(name: str, value: str) -> bytes:
    name_bytes = name.encode("utf-8")
    value_bytes = value.encode("utf-8")
    return bytes([len(name_bytes)]) + name_bytes + b"\x07" + struct.pack("!H", len(value_bytes)) + value_bytes


# ---------------------------------------------------------------------------
# Proxy handler
# ---------------------------------------------------------------------------


async def proxy_handler(request: web.Request) -> web.StreamResponse:
    # Reject requests to unknown paths (scanner/crawler protection)
    ctx: dict = request.app["trace_ctx"]
    extra_prefixes: tuple[str, ...] = ctx.get("extra_allowed_path_prefixes", ())
    if not _is_allowed_path(request.path, extra_prefixes):
        log.debug(f"Blocked non-API path: {request.method} {request.path}")
        return web.Response(status=404, text="Not Found")

    # Detect WebSocket upgrade and route to WS proxy.
    if request.headers.get("Upgrade", "").lower() == "websocket":
        if ctx.get("force_http"):
            log.info(f"Rejecting WebSocket upgrade on {request.path} (force_http); client will fallback to HTTP")
            return web.Response(status=426, text="Upgrade Required")
        from claude_tap.ws_proxy import _handle_websocket

        return await _handle_websocket(request)

    target: str = ctx["target_url"]
    writer: TraceWriter = ctx["writer"]
    session: aiohttp.ClientSession = ctx["session"]

    # Strip path prefix (e.g. /v1) for codex client so that
    # /v1/responses -> target + /responses
    strip_prefix: str = ctx.get("strip_path_prefix", "")
    fwd_path = request.raw_path
    if strip_prefix and fwd_path.startswith(strip_prefix):
        fwd_path = fwd_path[len(strip_prefix) :] or "/"
    upstream_url = build_upstream_url(target, fwd_path)

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

    req_body = _parse_request_body_for_trace(body)

    upstream_body = body
    if isinstance(req_body, dict):
        normalized_req_body = _normalize_request_body_for_upstream(req_body, target)
        if normalized_req_body is not req_body:
            req_body = normalized_req_body
            upstream_body = json.dumps(req_body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            for key in list(fwd_headers.keys()):
                if key.lower() == "content-length":
                    del fwd_headers[key]

    is_streaming = is_capture_only_streaming_request(request.raw_path, req_body)

    ctx["turn_counter"] = ctx.get("turn_counter", 0) + 1
    turn = ctx["turn_counter"]

    model = req_body.get("model", "") if isinstance(req_body, dict) else ""
    log_prefix = f"[Turn {turn}]"
    log.info(
        f"{log_prefix} → {request.method} {request.path} (model={model}, stream={is_streaming}, upstream={upstream_url})"
    )

    if ctx.get("capture_only") and is_capture_only_request(request.raw_path, req_body):
        resp_body = capture_only_response(request.raw_path, req_body)
        content_type = capture_only_content_type(request.raw_path, is_streaming)
        response_headers = {"Content-Type": content_type}
        duration_ms = int((time.monotonic() - t0) * 1000)
        record = _build_record(
            req_id,
            turn,
            duration_ms,
            request.method,
            request.raw_path,
            request.headers,
            req_body,
            200,
            response_headers,
            resp_body,
            upstream_base_url=target,
        )
        await writer.write(record)
        log.info(f"{log_prefix} ← 200 capture-only ({duration_ms}ms, upstream skipped)")
        if is_streaming:
            return web.Response(body=capture_only_stream_bytes(request.raw_path, req_body), content_type=content_type)
        return web.json_response(resp_body)

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
        error_text = format_upstream_error(exc, target_url=target, upstream_url=upstream_url)
        log.error(
            f"{log_prefix} upstream error while requesting {upstream_url}: {error_text}  "
            f"-- Check that the target ({target}) is reachable."
        )
        return web.Response(status=502, text=error_text)

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
            store_stream_events=bool(ctx.get("store_stream_events", False)),
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
    store_stream_events: bool,
) -> web.StreamResponse:
    resp = web.StreamResponse(
        status=upstream_resp.status,
        headers={k: v for k, v in upstream_resp.headers.items() if k.lower() not in HOP_BY_HOP},
    )
    await resp.prepare(request)

    is_bedrock_stream = is_bedrock_eventstream_path(request.raw_path)
    reassembler = SSEReassembler(store_events=store_stream_events)
    raw_chunks: list[bytes] = []

    try:
        async for chunk in upstream_resp.content.iter_any():
            await resp.write(chunk)
            if is_bedrock_stream:
                raw_chunks.append(chunk)
            else:
                reassembler.feed_bytes(chunk)
    except (ConnectionError, asyncio.CancelledError):
        pass

    try:
        await resp.write_eof()
    except (ConnectionError, ConnectionResetError, Exception):
        pass

    duration_ms = int((time.monotonic() - t0) * 1000)

    if is_bedrock_stream:
        raw_body = b"".join(raw_chunks).decode("utf-8", errors="replace")
        bedrock_events = _decode_bedrock_eventstream_events(raw_body)
        for event in bedrock_events:
            reassembler.add_event(event["event"], event["data"])
        reconstructed = reassembler.reconstruct()
        if not reconstructed:
            reconstructed = raw_body
        reconstructed = attach_bedrock_errors(reconstructed, bedrock_events)
    else:
        reconstructed = reassembler.reconstruct()

    usage = normalize_usage(reconstructed.get("usage", {}) if isinstance(reconstructed, dict) else {})
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
        request.raw_path,
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
        request.raw_path,
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
            "headers": filter_headers(resp_headers, redact_keys=True),
            "body": resp_body,
        },
    }
    if sse_events:
        record["response"]["sse_events"] = sse_events
    if upstream_base_url:
        record["upstream_base_url"] = upstream_base_url
    return record
