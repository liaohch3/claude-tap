from __future__ import annotations

import asyncio
import json

import aiohttp
import pytest

import claude_tap.codex_app_cdp as cdp
from claude_tap.cli import parse_args
from claude_tap.codex_app_cdp import (
    CodexAppCdpRecorder,
    _CdpClient,
    build_cdp_websocket_record,
    capture_codex_app_cdp,
    resolve_cdp_websocket_url,
    select_cdp_target,
    watch_codex_app_cdp,
)


class _FakeWriter:
    def __init__(self) -> None:
        self.count = 0
        self.records: list[dict] = []

    async def write(self, record: dict) -> None:
        self.records.append(record)
        self.count += 1

    async def write_next_turn(self, record: dict) -> None:
        record["turn"] = self.count + 1
        await self.write(record)


def _frame(payload: dict) -> dict:
    return {"response": {"opcode": 1, "payloadData": json.dumps(payload)}}


class _FakeHttpResponse:
    def __init__(self, status: int, payload: object = None, json_error: Exception | None = None) -> None:
        self.status = status
        self._payload = payload
        self._json_error = json_error

    async def __aenter__(self) -> "_FakeHttpResponse":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def json(self, *, content_type: object = None) -> object:
        if self._json_error:
            raise self._json_error
        return self._payload


class _FakeWsMessage:
    def __init__(self, data: object, msg_type: aiohttp.WSMsgType = aiohttp.WSMsgType.TEXT) -> None:
        self.type = msg_type
        self.data = data


class _FakeWebSocket:
    def __init__(self, messages: list[_FakeWsMessage]) -> None:
        self._messages = list(messages)
        self.sent: list[dict] = []

    async def __aenter__(self) -> "_FakeWebSocket":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    def __aiter__(self) -> "_FakeWebSocket":
        return self

    async def __anext__(self) -> _FakeWsMessage:
        if not self._messages:
            raise StopAsyncIteration
        return self._messages.pop(0)

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


class _FakeClientSession:
    def __init__(self, responses: list[_FakeHttpResponse], websocket: _FakeWebSocket | None = None) -> None:
        self._responses = list(responses)
        self.websocket = websocket
        self.requested_urls: list[str] = []

    async def __aenter__(self) -> "_FakeClientSession":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    def get(self, url: str, **_: object) -> _FakeHttpResponse:
        self.requested_urls.append(url)
        return self._responses.pop(0)

    def ws_connect(self, url: str, **_: object) -> _FakeWebSocket:
        assert self.websocket is not None
        self.requested_urls.append(url)
        return self.websocket


def _ws_text(payload: dict) -> _FakeWsMessage:
    return _FakeWsMessage(json.dumps(payload))


def test_select_cdp_target_prefers_codex_app_surface() -> None:
    targets = [
        {
            "type": "background_page",
            "title": "Codex Background",
            "url": "chrome-extension://codex",
            "webSocketDebuggerUrl": "ws://127.0.0.1/background",
        },
        {
            "type": "page",
            "title": "DevTools",
            "url": "devtools://devtools/bundled/inspector.html",
            "webSocketDebuggerUrl": "ws://127.0.0.1/devtools",
        },
        {
            "type": "webview",
            "title": "Codex",
            "url": "file:///Applications/Codex.app/index.html",
            "webSocketDebuggerUrl": "ws://127.0.0.1/codex",
        },
    ]

    assert select_cdp_target(targets) == "ws://127.0.0.1/codex"


def test_select_cdp_target_scores_common_page_shapes() -> None:
    targets = [
        {"type": "page", "title": "", "url": "about:blank", "webSocketDebuggerUrl": "ws://blank"},
        {
            "type": "iframe",
            "title": "Codex frame",
            "url": "https://127.0.0.1/frame",
            "webSocketDebuggerUrl": "ws://frame",
        },
        {"type": "app", "title": "Codex", "url": "https://localhost/app", "webSocketDebuggerUrl": "ws://app"},
        {"type": "page", "title": "", "url": "", "webSocketDebuggerUrl": ""},
        {"type": "page", "title": "DevTools", "url": "devtools://inspector", "webSocketDebuggerUrl": "ws://devtools"},
    ]

    assert select_cdp_target(targets) == "ws://app"
    assert (
        select_cdp_target([{"type": "service_worker", "title": "Codex", "webSocketDebuggerUrl": "ws://worker"}]) is None
    )


@pytest.mark.asyncio
async def test_resolve_cdp_websocket_url_accepts_direct_and_target_lists() -> None:
    assert await resolve_cdp_websocket_url(" ws://127.0.0.1/devtools/page/1 ", _FakeClientSession([])) == (
        "ws://127.0.0.1/devtools/page/1"
    )

    session = _FakeClientSession(
        [
            _FakeHttpResponse(500, []),
            _FakeHttpResponse(
                200,
                [{"type": "page", "title": "Codex", "url": "app://-/index.html", "webSocketDebuggerUrl": "ws://page"}],
            ),
        ]
    )

    assert await resolve_cdp_websocket_url("http://127.0.0.1:9238/", session) == "ws://page"
    assert session.requested_urls == ["http://127.0.0.1:9238/json/list", "http://127.0.0.1:9238/json"]


@pytest.mark.asyncio
async def test_resolve_cdp_websocket_url_reports_missing_targets() -> None:
    session = _FakeClientSession(
        [
            _FakeHttpResponse(200, {"description": "no websocket target"}),
            _FakeHttpResponse(200, [], json.JSONDecodeError("bad json", "", 0)),
        ]
    )

    with pytest.raises(RuntimeError, match="no page target"):
        await resolve_cdp_websocket_url("http://127.0.0.1:9238", session)


@pytest.mark.asyncio
async def test_cdp_recorder_writes_viewer_friendly_websocket_record() -> None:
    writer = _FakeWriter()
    recorder = CodexAppCdpRecorder(writer, store_stream_events=True, endpoint="http://127.0.0.1:9238")

    await recorder.handle_event(
        "Network.webSocketCreated",
        {"requestId": "ws-1", "url": "wss://chatgpt.com/backend-api/codex/responses?x=1"},
    )
    await recorder.handle_event(
        "Network.webSocketWillSendHandshakeRequest",
        {"requestId": "ws-1", "request": {"headers": {"Authorization": "Bearer secret-token-value"}}},
    )
    await recorder.handle_event(
        "Network.webSocketHandshakeResponseReceived",
        {"requestId": "ws-1", "response": {"status": 101, "headers": {"Server": "cloudflare"}}},
    )
    await recorder.handle_event(
        "Network.webSocketFrameSent",
        {
            "requestId": "ws-1",
            **_frame(
                {
                    "type": "response.create",
                    "model": "gpt-5.5",
                    "instructions": "You are Codex.",
                    "input": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "hello from cdp"}],
                        }
                    ],
                    "stream": True,
                }
            ),
        },
    )
    await recorder.handle_event(
        "Network.webSocketFrameReceived",
        {"requestId": "ws-1", **_frame({"type": "response.created", "response": {"id": "resp-1"}})},
    )
    await recorder.handle_event(
        "Network.webSocketFrameReceived",
        {
            "requestId": "ws-1",
            **_frame(
                {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "hello back"}],
                    },
                }
            ),
        },
    )
    await recorder.handle_event(
        "Network.webSocketFrameReceived",
        {
            "requestId": "ws-1",
            **_frame(
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp-1",
                        "model": "gpt-5.5",
                        "output": [],
                        "usage": {"input_tokens": 10, "output_tokens": 3, "total_tokens": 13},
                    },
                }
            ),
        },
    )

    assert len(writer.records) == 1
    record = writer.records[0]
    assert record["transport"] == "websocket"
    assert record["upstream_base_url"] == "https://chatgpt.com"
    assert record["request"]["method"] == "WEBSOCKET"
    assert record["request"]["path"] == "/backend-api/codex/responses?x=1"
    assert record["request"]["headers"]["Authorization"] == "Bearer secre..."
    assert record["request"]["body"]["model"] == "gpt-5.5"
    assert record["request"]["body"]["input"][0]["content"][0]["text"] == "hello from cdp"
    assert record["response"]["status"] == 101
    assert record["response"]["headers"]["Server"] == "cloudflare"
    assert record["response"]["body"]["usage"]["output_tokens"] == 3
    assert record["response"]["body"]["output"][0]["content"][0]["text"] == "hello back"
    assert record["request"]["ws_events"][0]["type"] == "response.create"
    assert [event["type"] for event in record["response"]["ws_events"]] == [
        "response.created",
        "response.output_item.done",
        "response.completed",
    ]
    assert record["capture"]["source"] == "codexapp-cdp"
    assert record["capture"]["cdp_request_id"] == "ws-1"
    assert record["capture"]["cdp_endpoint"] == "http://127.0.0.1:9238"


@pytest.mark.asyncio
async def test_cdp_recorder_assigns_sequential_responses_to_request_frames() -> None:
    writer = _FakeWriter()
    recorder = CodexAppCdpRecorder(writer)

    await recorder.handle_event("Network.webSocketCreated", {"requestId": "ws-2", "url": "wss://api.test/v1/responses"})
    for index in range(2):
        await recorder.handle_event(
            "Network.webSocketFrameSent",
            {
                "requestId": "ws-2",
                **_frame({"type": "response.create", "model": f"model-{index}", "input": [{"turn": index}]}),
            },
        )
    for index in range(2):
        response_id = f"resp-{index}"
        await recorder.handle_event(
            "Network.webSocketFrameReceived",
            {"requestId": "ws-2", **_frame({"type": "response.created", "response": {"id": response_id}})},
        )
        await recorder.handle_event(
            "Network.webSocketFrameReceived",
            {"requestId": "ws-2", **_frame({"type": "response.completed", "response": {"id": response_id}})},
        )

    assert [record["request"]["body"]["model"] for record in writer.records] == ["model-0", "model-1"]
    assert [record["response"]["body"]["id"] for record in writer.records] == ["resp-0", "resp-1"]
    assert [record["turn"] for record in writer.records] == [1, 2]


@pytest.mark.asyncio
async def test_cdp_recorder_ignores_duplicate_completed_events_for_flushed_response() -> None:
    writer = _FakeWriter()
    recorder = CodexAppCdpRecorder(writer)

    await recorder.handle_event("Network.webSocketCreated", {"requestId": "ws-dup", "url": "wss://api.test/v1"})
    await recorder.handle_event(
        "Network.webSocketFrameSent",
        {"requestId": "ws-dup", **_frame({"type": "response.create", "model": "gpt", "input": []})},
    )
    completed = {"requestId": "ws-dup", **_frame({"type": "response.completed", "response": {"id": "resp-dup"}})}

    await recorder.handle_event("Network.webSocketFrameReceived", completed)
    await recorder.handle_event("Network.webSocketFrameReceived", completed)

    assert len(writer.records) == 1
    assert writer.records[0]["response"]["body"]["id"] == "resp-dup"


@pytest.mark.asyncio
async def test_cdp_recorder_flushes_incomplete_response_on_socket_close() -> None:
    writer = _FakeWriter()
    recorder = CodexAppCdpRecorder(writer)

    await recorder.handle_event("Network.webSocketWillSendHandshakeRequest", {"requestId": "missing"})
    await recorder.handle_event("Network.webSocketCreated", {"requestId": "ws-close", "url": "wss://api.test/v1"})
    await recorder.handle_event("Network.webSocketHandshakeResponseReceived", {"requestId": "missing"})
    await recorder.handle_event(
        "Network.webSocketFrameSent", {"requestId": "ws-close", "response": {"payloadData": "not-json"}}
    )
    await recorder.handle_event(
        "Network.webSocketFrameReceived", {"requestId": "ws-close", "response": {"payloadData": "not-json"}}
    )
    await recorder.handle_event(
        "Network.webSocketFrameReceived",
        {"requestId": "ws-close", **_frame({"type": "response.created", "response": {"id": "resp-close"}})},
    )
    await recorder.handle_event("Network.webSocketClosed", {"requestId": "ws-close"})
    await recorder.flush_all(error="unused")
    await recorder._flush_socket("missing")

    assert len(writer.records) == 1
    record = writer.records[0]
    assert record["response"]["body"]["id"] == "resp-close"
    assert record["response"]["error"] == "CDP websocket closed before response.completed"


def test_build_cdp_websocket_record_omits_raw_events_by_default() -> None:
    record = build_cdp_websocket_record(
        url="ws://localhost/v1/responses",
        cdp_request_id="ws-raw",
        request_messages=[json.dumps({"type": "response.create", "model": "gpt"})],
        response_events=[{"type": "response.completed", "response": {"id": "resp"}}],
        request_headers={},
        response_headers={},
        response_status=101,
        duration_ms=1,
        turn=1,
        store_stream_events=False,
        endpoint="",
    )

    assert record["request"]["body"]["model"] == "gpt"
    assert record["response"]["body"]["id"] == "resp"
    assert "ws_events" not in record["request"]
    assert "ws_events" not in record["response"]


def test_build_cdp_websocket_record_preserves_error_and_raw_request_events() -> None:
    record = build_cdp_websocket_record(
        url="wss://chatgpt.com",
        cdp_request_id="ws-error",
        request_messages=["not-json"],
        response_events=[],
        request_headers={"Cookie": "secret"},
        response_headers={},
        response_status=101,
        duration_ms=1,
        turn=2,
        store_stream_events=True,
        endpoint="",
        error="closed",
    )

    assert record["upstream_base_url"] == "https://chatgpt.com"
    assert record["request"]["path"] == "/"
    assert record["request"]["ws_events"] == [{"raw": "not-json"}]
    assert record["response"]["error"] == "closed"


@pytest.mark.asyncio
async def test_cdp_client_runs_network_enable_and_dispatches_events() -> None:
    writer = _FakeWriter()
    recorder = CodexAppCdpRecorder(writer)
    websocket = _FakeWebSocket(
        [
            _FakeWsMessage(b"binary", aiohttp.WSMsgType.BINARY),
            _FakeWsMessage("not-json"),
            _ws_text([]),
            _ws_text({"id": 1, "result": {}}),
            _ws_text(
                {"method": "Network.webSocketCreated", "params": {"requestId": "ws-client", "url": "wss://api.test/v1"}}
            ),
            _ws_text(
                {
                    "method": "Network.webSocketFrameSent",
                    "params": {
                        "requestId": "ws-client",
                        **_frame({"type": "response.create", "model": "gpt", "input": [{"content": "hi"}]}),
                    },
                }
            ),
            _ws_text(
                {
                    "method": "Network.webSocketFrameReceived",
                    "params": {
                        "requestId": "ws-client",
                        **_frame({"type": "response.created", "response": {"id": "resp-client"}}),
                    },
                }
            ),
            _ws_text(
                {
                    "method": "Network.webSocketFrameReceived",
                    "params": {
                        "requestId": "ws-client",
                        **_frame({"type": "response.completed", "response": {"id": "resp-client"}}),
                    },
                }
            ),
        ]
    )

    await _CdpClient(websocket, recorder).run()

    assert websocket.sent == [{"id": 1, "method": "Network.enable", "params": {}}]
    assert writer.records[0]["request"]["body"]["model"] == "gpt"
    assert writer.records[0]["response"]["body"]["id"] == "resp-client"


@pytest.mark.asyncio
async def test_capture_codex_app_cdp_connects_and_flushes(monkeypatch: pytest.MonkeyPatch) -> None:
    writer = _FakeWriter()
    websocket = _FakeWebSocket(
        [
            _ws_text({"id": 1, "result": {}}),
            _ws_text(
                {
                    "method": "Network.webSocketCreated",
                    "params": {"requestId": "ws-capture", "url": "wss://api.test/v1"},
                }
            ),
            _ws_text(
                {
                    "method": "Network.webSocketFrameSent",
                    "params": {
                        "requestId": "ws-capture",
                        **_frame({"type": "response.create", "model": "gpt-capture", "input": [{"content": "hi"}]}),
                    },
                }
            ),
            _ws_text(
                {
                    "method": "Network.webSocketFrameReceived",
                    "params": {
                        "requestId": "ws-capture",
                        **_frame({"type": "response.created", "response": {"id": "resp-capture"}}),
                    },
                }
            ),
        ]
    )
    session = _FakeClientSession(
        [
            _FakeHttpResponse(
                200,
                [{"type": "page", "title": "Codex", "url": "app://-/index.html", "webSocketDebuggerUrl": "ws://page"}],
            )
        ],
        websocket=websocket,
    )
    monkeypatch.setattr(cdp.aiohttp, "ClientSession", lambda: session)

    await capture_codex_app_cdp(writer, endpoint="http://127.0.0.1:9238")

    assert session.requested_urls == ["http://127.0.0.1:9238/json/list", "ws://page"]
    assert writer.records[0]["response"]["body"]["id"] == "resp-capture"


@pytest.mark.asyncio
async def test_watch_codex_app_cdp_reconnects_and_propagates_cancel(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls = 0

    async def fake_capture(*_: object, **__: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("no cdp")
        raise asyncio.CancelledError

    async def fake_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(cdp, "capture_codex_app_cdp", fake_capture)
    monkeypatch.setattr(cdp.asyncio, "sleep", fake_sleep)

    caplog.set_level("DEBUG")
    with pytest.raises(asyncio.CancelledError):
        await watch_codex_app_cdp(_FakeWriter(), reconnect_interval=0)

    assert calls == 2
    assert "Codex App CDP capture unavailable: no cdp" in caplog.text


@pytest.mark.asyncio
async def test_async_main_codexapp_starts_cdp_enrichment_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from claude_tap import async_main

    started = asyncio.Event()
    cancelled = asyncio.Event()
    calls: list[dict[str, object]] = []

    async def fake_watch_codex_app_cdp(*args: object, **kwargs: object) -> None:
        calls.append(kwargs)
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def fake_watch_codex_app_transcripts_to_sessions(*args: object, **kwargs: object) -> None:
        await asyncio.wait_for(started.wait(), timeout=1)

    monkeypatch.setenv("CLOUDTAP_DB", str(tmp_path / "codexapp-auto-cdp.sqlite3"))
    monkeypatch.setattr("claude_tap.cli.watch_codex_app_cdp", fake_watch_codex_app_cdp)
    monkeypatch.setattr(
        "claude_tap.cli.watch_codex_app_transcripts_to_sessions",
        fake_watch_codex_app_transcripts_to_sessions,
    )

    args = parse_args(["--tap-client", "codexapp", "--tap-no-live", "--tap-no-open", "--tap-max-traces", "0"])

    assert args.store_stream_events is False
    assert await async_main(args) == 0
    assert cancelled.is_set()
    assert calls == [{"endpoint": "http://127.0.0.1:9238", "store_stream_events": False}]


def test_parse_args_codexapp_uses_default_cdp_endpoint_for_automatic_capture() -> None:
    args = parse_args(["--tap-client", "codexapp"])

    assert args.client == "codexapp"
    assert args.codexapp_cdp_endpoint == "http://127.0.0.1:9238"


def test_parse_args_rejects_custom_cdp_endpoint_for_non_codexapp() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--tap-client", "codex", "--tap-codexapp-cdp-endpoint", "http://127.0.0.1:9999"])


def test_parse_args_rejects_removed_cdp_capture_flag() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--tap-client", "codexapp", "--tap-codexapp-cdp-capture"])
