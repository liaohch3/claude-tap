"""Codex App CDP capture for best-effort websocket trace evidence."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import aiohttp

from claude_tap.proxy import filter_headers
from claude_tap.trace import TraceWriter
from claude_tap.ws_proxy import reconstruct_ws_request_body, reconstruct_ws_response_body

log = logging.getLogger("claude-tap")

CODEX_APP_CDP_DEFAULT_ENDPOINT = "http://127.0.0.1:9238"
CODEX_APP_CDP_SOURCE = "codexapp-cdp"
_CDP_COMMAND_TIMEOUT = 10.0


@dataclass
class _CdpTarget:
    web_socket_debugger_url: str
    type: str = ""
    title: str = ""
    url: str = ""


@dataclass
class _CdpSocketState:
    request_id: str
    url: str
    created_at: float
    request_headers: dict[str, str] = field(default_factory=dict)
    response_headers: dict[str, str] = field(default_factory=dict)
    response_status: int = 101
    pending_request_messages: deque[str] = field(default_factory=deque)
    response_requests: dict[str, list[str]] = field(default_factory=dict)
    response_events: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    response_started_at: dict[str, float] = field(default_factory=dict)
    flushed_response_ids: set[str] = field(default_factory=set)
    active_response_id: str | None = None


def _string_headers(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    headers: dict[str, str] = {}
    for key, header_value in value.items():
        if isinstance(key, str):
            headers[key] = str(header_value)
    return headers


def _target_from_json(value: object) -> _CdpTarget | None:
    if not isinstance(value, dict):
        return None
    ws_url = value.get("webSocketDebuggerUrl")
    if not isinstance(ws_url, str) or not ws_url:
        return None
    return _CdpTarget(
        web_socket_debugger_url=ws_url,
        type=str(value.get("type") or ""),
        title=str(value.get("title") or ""),
        url=str(value.get("url") or ""),
    )


def select_cdp_target(targets: list[dict[str, Any]]) -> str | None:
    """Return the best page/webview CDP target URL from a /json target list."""
    parsed_targets = [target for target in (_target_from_json(item) for item in targets) if target is not None]
    scored = [
        (score, index, target)
        for index, target in enumerate(parsed_targets)
        if (score := _score_cdp_target(target)) != float("-inf")
    ]
    scored.sort(key=lambda item: (-item[0], item[1]))
    return scored[0][2].web_socket_debugger_url if scored else None


def _score_cdp_target(target: _CdpTarget) -> float:
    target_type = target.type.lower()
    title = target.title.lower()
    url = target.url.lower()
    haystack = f"{title} {url}"
    if not target.web_socket_debugger_url:
        return float("-inf")
    if not haystack.strip() and not target_type:
        return float("-inf")
    if "devtools" in haystack:
        return float("-inf")
    if target_type in {"background_page", "service_worker"}:
        return float("-inf")

    score = 0.0
    if target_type == "app":
        score += 120
    elif target_type == "webview":
        score += 100
    elif target_type == "page":
        score += 80
    elif target_type == "iframe":
        score += 20

    if "codex" in haystack:
        score += 120
    if url.startswith("http://localhost") or url.startswith("https://localhost"):
        score += 90
    if url.startswith("file://"):
        score += 60
    if url.startswith("http://127.0.0.1") or url.startswith("https://127.0.0.1"):
        score += 50
    if url.startswith("about:blank"):
        score -= 120
    if target.title:
        score += 25
    return score


async def resolve_cdp_websocket_url(endpoint: str, session: aiohttp.ClientSession) -> str:
    """Resolve an HTTP or WebSocket CDP endpoint to a page WebSocket URL."""
    endpoint = endpoint.strip()
    if endpoint.startswith(("ws://", "wss://")):
        return endpoint
    base = endpoint.rstrip("/")
    errors: list[str] = []
    for suffix in ("/json/list", "/json"):
        try:
            async with session.get(base + suffix, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status < 200 or resp.status >= 300:
                    errors.append(f"{suffix} HTTP {resp.status}")
                    continue
                payload = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as exc:
            errors.append(f"{suffix} {exc}")
            continue

        if isinstance(payload, dict):
            target = _target_from_json(payload)
            if target is not None:
                return target.web_socket_debugger_url
            payload = [payload]
        if isinstance(payload, list):
            ws_url = select_cdp_target(payload)
            if ws_url:
                return ws_url
            errors.append(f"{suffix} no page target")
    detail = "; ".join(errors) if errors else "no target metadata"
    raise RuntimeError(f"Could not resolve Codex App CDP target from {endpoint}: {detail}")


class CodexAppCdpRecorder:
    """Convert CDP Network websocket events into claude-tap trace records."""

    def __init__(self, writer: TraceWriter, *, store_stream_events: bool = False, endpoint: str = ""):
        self._writer = writer
        self._store_stream_events = store_stream_events
        self._endpoint = endpoint
        self._sockets: dict[str, _CdpSocketState] = {}

    async def handle_event(self, method: str, params: dict[str, Any]) -> None:
        if method == "Network.webSocketCreated":
            self._handle_websocket_created(params)
        elif method == "Network.webSocketWillSendHandshakeRequest":
            self._handle_websocket_request_headers(params)
        elif method == "Network.webSocketHandshakeResponseReceived":
            self._handle_websocket_response_headers(params)
        elif method == "Network.webSocketFrameSent":
            self._handle_websocket_frame_sent(params)
        elif method == "Network.webSocketFrameReceived":
            await self._handle_websocket_frame_received(params)
        elif method == "Network.webSocketClosed":
            await self._handle_websocket_closed(params)

    async def flush_all(self, *, error: str | None = None) -> None:
        for request_id in list(self._sockets):
            await self._flush_socket(request_id, error=error)

    def _handle_websocket_created(self, params: dict[str, Any]) -> None:
        request_id = _request_id(params)
        url = params.get("url")
        if request_id and isinstance(url, str) and url:
            self._sockets[request_id] = _CdpSocketState(request_id=request_id, url=url, created_at=time.monotonic())

    def _handle_websocket_request_headers(self, params: dict[str, Any]) -> None:
        state = self._state_for_params(params)
        if state is None:
            return
        request = params.get("request")
        if isinstance(request, dict):
            state.request_headers = _string_headers(request.get("headers"))

    def _handle_websocket_response_headers(self, params: dict[str, Any]) -> None:
        state = self._state_for_params(params)
        if state is None:
            return
        response = params.get("response")
        if isinstance(response, dict):
            status = response.get("status")
            if isinstance(status, int):
                state.response_status = status
            state.response_headers = _string_headers(response.get("headers"))

    def _handle_websocket_frame_sent(self, params: dict[str, Any]) -> None:
        state = self._state_for_params(params)
        payload = _frame_payload(params)
        if state is None or payload is None:
            return
        parsed = _json_object(payload)
        if parsed is None:
            return
        if parsed.get("type") == "response.create" or "model" in parsed or "input" in parsed:
            state.pending_request_messages.append(payload)

    async def _handle_websocket_frame_received(self, params: dict[str, Any]) -> None:
        state = self._state_for_params(params)
        payload = _frame_payload(params)
        if state is None or payload is None:
            return
        event = _json_object(payload)
        if event is None:
            return

        response_id = _response_id_from_event(event)
        if response_id is None:
            response_id = state.active_response_id
        if response_id is None:
            return
        if response_id in state.flushed_response_ids:
            return

        self._ensure_response_bucket(state, response_id)
        state.response_events[response_id].append(event)
        state.active_response_id = response_id

        if event.get("type") in {"response.completed", "response.done"}:
            await self._flush_response(state, response_id)

    async def _handle_websocket_closed(self, params: dict[str, Any]) -> None:
        request_id = _request_id(params)
        if request_id:
            await self._flush_socket(request_id, error="CDP websocket closed before response.completed")

    def _state_for_params(self, params: dict[str, Any]) -> _CdpSocketState | None:
        request_id = _request_id(params)
        return self._sockets.get(request_id) if request_id else None

    def _ensure_response_bucket(self, state: _CdpSocketState, response_id: str) -> None:
        if response_id not in state.response_requests:
            if state.pending_request_messages:
                state.response_requests[response_id] = [state.pending_request_messages.popleft()]
            else:
                state.response_requests[response_id] = []
        state.response_events.setdefault(response_id, [])
        state.response_started_at.setdefault(response_id, time.monotonic())

    async def _flush_socket(self, request_id: str, *, error: str | None = None) -> None:
        state = self._sockets.pop(request_id, None)
        if state is None:
            return
        for response_id in list(state.response_events):
            await self._flush_response(state, response_id, error=error)

    async def _flush_response(self, state: _CdpSocketState, response_id: str, *, error: str | None = None) -> None:
        request_messages = state.response_requests.pop(response_id, [])
        response_events = state.response_events.pop(response_id, [])
        started_at = state.response_started_at.pop(response_id, state.created_at)
        if not request_messages and not response_events:
            return

        record = build_cdp_websocket_record(
            url=state.url,
            cdp_request_id=state.request_id,
            request_messages=request_messages,
            response_events=response_events,
            request_headers=state.request_headers,
            response_headers=state.response_headers,
            response_status=state.response_status,
            duration_ms=max(0, int((time.monotonic() - started_at) * 1000)),
            turn=self._writer.count + 1,
            store_stream_events=self._store_stream_events,
            endpoint=self._endpoint,
            error=error,
        )
        await self._writer.write_next_turn(record)
        state.flushed_response_ids.add(response_id)
        if state.active_response_id == response_id:
            state.active_response_id = None


def build_cdp_websocket_record(
    *,
    url: str,
    cdp_request_id: str,
    request_messages: list[str],
    response_events: list[dict[str, Any]],
    request_headers: dict[str, str],
    response_headers: dict[str, str],
    response_status: int,
    duration_ms: int,
    turn: int,
    store_stream_events: bool,
    endpoint: str,
    error: str | None = None,
) -> dict[str, Any]:
    request_body = reconstruct_ws_request_body(request_messages)
    response_body = reconstruct_ws_response_body(response_events)
    parsed = urlsplit(url)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    upstream_scheme = "https" if parsed.scheme == "wss" else "http" if parsed.scheme == "ws" else parsed.scheme
    upstream_base_url = urlunsplit((upstream_scheme, parsed.netloc, "", "", ""))

    record: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": f"codex_app_cdp_{uuid.uuid4().hex[:12]}",
        "turn": turn,
        "duration_ms": duration_ms,
        "transport": "websocket",
        "upstream_base_url": upstream_base_url,
        "request": {
            "method": "WEBSOCKET",
            "path": path,
            "headers": filter_headers(request_headers, redact_keys=True),
            "body": request_body,
        },
        "response": {
            "status": response_status,
            "headers": filter_headers(response_headers, redact_keys=True),
            "body": response_body,
        },
        "capture": {
            "source": CODEX_APP_CDP_SOURCE,
            "cdp_request_id": cdp_request_id,
        },
    }
    if endpoint:
        record["capture"]["cdp_endpoint"] = endpoint
    if store_stream_events:
        request_events = [_json_object(message) or {"raw": message} for message in request_messages]
        if request_events:
            record["request"]["ws_events"] = request_events
        if response_events:
            record["response"]["ws_events"] = response_events
    if error:
        record["response"]["error"] = error
    return record


async def capture_codex_app_cdp(
    writer: TraceWriter,
    *,
    endpoint: str = CODEX_APP_CDP_DEFAULT_ENDPOINT,
    store_stream_events: bool = False,
) -> None:
    """Capture Codex App websocket frames from one CDP connection until it closes."""
    async with aiohttp.ClientSession() as session:
        ws_url = await resolve_cdp_websocket_url(endpoint, session)
        async with session.ws_connect(ws_url, heartbeat=20) as ws:
            recorder = CodexAppCdpRecorder(writer, store_stream_events=store_stream_events, endpoint=endpoint)
            client = _CdpClient(ws, recorder)
            try:
                await client.run()
            finally:
                await recorder.flush_all()


async def watch_codex_app_cdp(
    writer: TraceWriter,
    *,
    endpoint: str = CODEX_APP_CDP_DEFAULT_ENDPOINT,
    store_stream_events: bool = False,
    reconnect_interval: float = 10.0,
) -> None:
    """Keep reconnecting to Codex App CDP and append captured websocket records."""
    last_error: str | None = None
    while True:
        try:
            await capture_codex_app_cdp(writer, endpoint=endpoint, store_stream_events=store_stream_events)
            last_error = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = str(exc)
            if error != last_error:
                log.debug("Codex App CDP capture unavailable: %s", exc)
                last_error = error
        await asyncio.sleep(reconnect_interval)


class _CdpClient:
    def __init__(self, ws: aiohttp.ClientWebSocketResponse, recorder: CodexAppCdpRecorder):
        self._ws = ws
        self._recorder = recorder
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}

    async def run(self) -> None:
        receiver = asyncio.create_task(self._receive_loop())
        try:
            await self.send("Network.enable")
            await receiver
        finally:
            receiver.cancel()
            try:
                await receiver
            except asyncio.CancelledError:
                pass

    async def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        message_id = self._next_id
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[message_id] = future
        await self._ws.send_json({"id": message_id, "method": method, "params": params or {}})
        return await asyncio.wait_for(future, timeout=_CDP_COMMAND_TIMEOUT)

    async def _receive_loop(self) -> None:
        async for msg in self._ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            try:
                payload = json.loads(msg.data)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            message_id = payload.get("id")
            if isinstance(message_id, int) and message_id in self._pending:
                future = self._pending.pop(message_id)
                if not future.done():
                    future.set_result(payload)
                continue
            method = payload.get("method")
            params = payload.get("params")
            if isinstance(method, str) and isinstance(params, dict):
                await self._recorder.handle_event(method, params)


def _request_id(params: dict[str, Any]) -> str:
    value = params.get("requestId")
    return value if isinstance(value, str) else ""


def _frame_payload(params: dict[str, Any]) -> str | None:
    response = params.get("response")
    if not isinstance(response, dict):
        return None
    payload = response.get("payloadData")
    return payload if isinstance(payload, str) else None


def _json_object(value: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _response_id_from_event(event: dict[str, Any]) -> str | None:
    response = event.get("response")
    if isinstance(response, dict):
        response_id = response.get("id")
        if isinstance(response_id, str) and response_id:
            return response_id
    response_id = event.get("response_id")
    if isinstance(response_id, str) and response_id:
        return response_id
    return None
