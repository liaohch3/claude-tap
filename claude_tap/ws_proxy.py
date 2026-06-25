"""WebSocket proxy – forward WS connections to upstream and record traces."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import deque
from datetime import datetime, timezone

import aiohttp
from aiohttp import web
from aiohttp.helpers import get_env_proxy_for_url
from yarl import URL

from claude_tap.proxy import capture_only_response, filter_headers, is_capture_only_request
from claude_tap.trace import TraceWriter
from claude_tap.upstream import build_upstream_url, format_upstream_error

log = logging.getLogger("claude-tap")

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
_COMPLETED_RESPONSE_KEY_CACHE_SIZE = 1024


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
    store_stream_events = bool(ctx.get("store_stream_events", False))

    strip_prefix: str = ctx.get("strip_path_prefix", "")
    fwd_path = request.path_qs
    if strip_prefix and fwd_path.startswith(strip_prefix):
        fwd_path = fwd_path[len(strip_prefix) :] or "/"
    upstream_url = build_upstream_url(target, fwd_path)

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

    if ctx.get("capture_only"):
        return await _handle_capture_only_websocket(
            request=request,
            writer=writer,
            target=target,
            protocols=protocols,
            req_id=req_id,
            turn=turn,
            t0=t0,
            log_prefix=log_prefix,
            store_stream_events=store_stream_events,
        )

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
        error_message = format_upstream_error(exc, target_url=target, upstream_url=upstream_ws_url)
        log.error(f"{log_prefix} upstream WS connect to {upstream_ws_url} failed: {error_message}")
        record = _build_ws_record(
            req_id=req_id,
            turn=turn,
            duration_ms=duration_ms,
            path_qs=request.path_qs,
            req_headers=request.headers,
            client_messages=[],
            server_messages=[],
            upstream_base_url=target,
            error=error_message,
            store_stream_events=store_stream_events,
        )
        await writer.write(record)
        return web.Response(status=502, text=error_message)

    # Upstream connected — accept client WebSocket upgrade
    client_ws = web.WebSocketResponse(protocols=protocols)
    await client_ws.prepare(request)

    client_messages: list[str] = []
    server_messages: list[str] = []
    client_message_count = 0
    server_message_count = 0
    completed_records_written = 0
    completed_response_keys: set[str] = set()
    completed_response_key_order: deque[str] = deque()
    pending_write: asyncio.Task[None] | None = None

    def _pop_buffered_snapshot() -> tuple[int, list[str], list[str]]:
        nonlocal completed_records_written
        completed_records_written += 1
        record_client_messages = client_messages.copy()
        record_server_messages = server_messages.copy()
        client_messages.clear()
        server_messages.clear()
        return completed_records_written, record_client_messages, record_server_messages

    def _pop_buffered_server_snapshot() -> tuple[int, list[str], list[str]]:
        nonlocal completed_records_written
        completed_records_written += 1
        record_server_messages = server_messages.copy()
        server_messages.clear()
        return completed_records_written, [], record_server_messages

    async def _write_buffered_snapshot(snapshot: tuple[int, list[str], list[str]]) -> None:
        record_number, record_client_messages, record_server_messages = snapshot
        record = _build_ws_record(
            req_id=req_id if record_number == 1 else f"{req_id}_{record_number}",
            turn=turn if record_number == 1 else f"{turn}.{record_number}",
            duration_ms=int((time.monotonic() - t0) * 1000),
            path_qs=request.path_qs,
            req_headers=request.headers,
            client_messages=record_client_messages,
            server_messages=record_server_messages,
            upstream_base_url=target,
            store_stream_events=store_stream_events,
        )
        await writer.write(record)

    async def _write_buffered_record() -> None:
        await _write_buffered_snapshot(_pop_buffered_snapshot())

    def _schedule_buffered_snapshot(snapshot: tuple[int, list[str], list[str]]) -> None:
        nonlocal pending_write
        previous_write = pending_write

        async def _write_after_previous() -> None:
            if previous_write is not None:
                await previous_write
            await _write_buffered_snapshot(snapshot)

        pending_write = asyncio.create_task(_write_after_previous())

    async def _drain_pending_write() -> None:
        if pending_write is not None:
            await asyncio.shield(pending_write)

    def _pop_completed_snapshot(response_key: str, terminal_message: str) -> tuple[int, list[str], list[str]] | None:
        if response_key in completed_response_keys:
            if server_messages and server_messages[-1] == terminal_message:
                server_messages.pop()
            if server_messages:
                return _pop_buffered_server_snapshot()
            return None
        completed_response_keys.add(response_key)
        completed_response_key_order.append(response_key)
        if len(completed_response_key_order) > _COMPLETED_RESPONSE_KEY_CACHE_SIZE:
            expired = completed_response_key_order.popleft()
            completed_response_keys.discard(expired)
        return _pop_buffered_snapshot()

    async def _relay_client_to_upstream():
        nonlocal client_message_count
        try:
            async for msg in client_ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    client_message_count += 1
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
        nonlocal server_message_count
        try:
            async for msg in upstream_ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    server_message_count += 1
                    server_messages.append(msg.data)
                    response_key = _response_completed_message_key(msg.data)
                    completed_snapshot = (
                        _pop_completed_snapshot(response_key, msg.data) if response_key is not None else None
                    )
                    try:
                        await client_ws.send_str(msg.data)
                    finally:
                        if completed_snapshot is not None:
                            _schedule_buffered_snapshot(completed_snapshot)
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

    await _drain_pending_write()

    if client_messages or server_messages:
        await _write_buffered_record()

    log.info(
        f"{log_prefix} ← WS closed ({duration_ms}ms, "
        f"{client_message_count} client→upstream, "
        f"{server_message_count} upstream→client)"
    )

    return client_ws


async def _handle_capture_only_websocket(
    *,
    request: web.Request,
    writer: TraceWriter,
    target: str,
    protocols: tuple[str, ...],
    req_id: str,
    turn: int,
    t0: float,
    log_prefix: str,
    store_stream_events: bool,
) -> web.WebSocketResponse:
    client_ws = web.WebSocketResponse(protocols=protocols)
    await client_ws.prepare(request)

    client_messages: list[str] = []
    deadline = time.monotonic() + 30
    while True:
        timeout = max(0.1, deadline - time.monotonic())
        try:
            msg = await asyncio.wait_for(client_ws.receive(), timeout=timeout)
        except asyncio.TimeoutError:
            break

        if msg.type == aiohttp.WSMsgType.TEXT:
            client_messages.append(msg.data)
            req_body = _reconstruct_ws_request_body(client_messages) or {}
            if is_prompt_bearing_ws_request_body(req_body):
                break
            if time.monotonic() >= deadline:
                break
            continue
        if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED):
            break
        if msg.type == aiohttp.WSMsgType.ERROR:
            break

    req_body = _reconstruct_ws_request_body(client_messages) or {}
    response_body = capture_only_response(request.path_qs, req_body)
    response_messages = [
        json.dumps({"type": "response.created", "response": {**response_body, "status": "in_progress"}}),
        json.dumps({"type": "response.completed", "response": {**response_body, "status": "completed"}}),
    ]
    for message in response_messages:
        await client_ws.send_str(message)
    await client_ws.close()

    duration_ms = int((time.monotonic() - t0) * 1000)
    record = _build_ws_record(
        req_id=req_id,
        turn=turn,
        duration_ms=duration_ms,
        path_qs=request.path_qs,
        req_headers=request.headers,
        client_messages=client_messages,
        server_messages=response_messages,
        upstream_base_url=target,
        store_stream_events=store_stream_events,
    )
    await writer.write(record)
    log.info(f"{log_prefix} ← WS capture-only ({duration_ms}ms, upstream skipped)")
    return client_ws


def _build_ws_record(
    req_id: str,
    turn: int | str,
    duration_ms: int,
    path_qs: str,
    req_headers: dict,
    client_messages: list[str],
    server_messages: list[str],
    upstream_base_url: str,
    error: str | None = None,
    store_stream_events: bool = True,
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
    req_events = _parse_ws_messages(client_messages)

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
            "status": 101 if error is None else 502,
            "headers": {},
            "body": resp_body,
        },
    }
    if store_stream_events and ws_events:
        record["response"]["ws_events"] = ws_events
    if store_stream_events and req_events:
        record["request"]["ws_events"] = req_events
    if error is not None:
        record["response"]["error"] = error
    if upstream_base_url:
        record["upstream_base_url"] = upstream_base_url
    return record


def _parse_ws_messages(messages: list[str]) -> list[dict]:
    parsed_messages: list[dict] = []
    for msg in messages:
        try:
            parsed = json.loads(msg)
            parsed_messages.append(parsed if isinstance(parsed, dict) else {"raw": parsed})
        except (json.JSONDecodeError, ValueError):
            parsed_messages.append({"raw": msg})
    return parsed_messages


def _response_completed_message_key(message: str) -> str | None:
    try:
        parsed = json.loads(message)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict) or parsed.get("type") not in ("response.completed", "response.done"):
        return None
    response = parsed.get("response")
    if isinstance(response, dict) and response.get("id"):
        return str(response["id"])
    return json.dumps(parsed, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


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
                if isinstance(merged.get(key), list) and isinstance(value, list):
                    merged[key] = _merge_json_lists(merged[key], value)
                elif value:
                    merged[key] = value
                else:
                    merged.setdefault(key, value)
                continue
            if value not in (None, "", [], {}):
                merged[key] = value
            else:
                merged.setdefault(key, value)
    return merged


def _merge_json_lists(existing: list, incoming: list) -> list:
    """Append JSON-like list items while preserving order and removing exact duplicates."""
    merged = list(existing)
    seen = {_json_list_item_key(item) for item in merged}
    for item in incoming:
        key = _json_list_item_key(item)
        if key in seen:
            continue
        merged.append(item)
        seen.add(key)
    return merged


def _json_list_item_key(item: object) -> str:
    try:
        return json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return repr(item)


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


def is_prompt_bearing_ws_request_body(body: dict | None) -> bool:
    """Return whether a reconstructed WebSocket request contains an actual prompt."""
    if not isinstance(body, dict):
        return False
    if not is_capture_only_request("", body):
        return False
    for key in ("system", "instructions", "system_instruction", "systemInstruction", "messages", "contents", "prompt"):
        if body.get(key):
            return True
    input_value = body.get("input")
    if isinstance(input_value, str):
        return bool(input_value.strip())
    if isinstance(input_value, list):
        return any(_ws_input_item_is_prompt(item) for item in input_value)
    nested = body.get("request")
    return isinstance(nested, dict) and is_prompt_bearing_ws_request_body(nested)


def _ws_input_item_is_prompt(item: object) -> bool:
    if isinstance(item, str):
        return bool(item.strip())
    if not isinstance(item, dict):
        return False
    if item.get("type") == "function_call_output":
        return False
    if item.get("role") in {"user", "developer", "system"}:
        return True
    return any(key in item for key in ("content", "text", "input_text"))
