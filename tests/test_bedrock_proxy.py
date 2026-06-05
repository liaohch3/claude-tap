"""Tests for Bedrock EventStream capture in reverse and forward proxy modes."""

from __future__ import annotations

import base64
import json
import struct
import zlib
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from aiohttp import web

from claude_tap.forward_proxy import ForwardProxyServer
from claude_tap.proxy import (
    capture_only_content_type,
    capture_only_response,
    capture_only_stream_bytes,
    is_capture_only_request,
    is_capture_only_streaming_request,
    proxy_handler,
)
from claude_tap.trace import TraceWriter
from claude_tap.trace_store import get_trace_store, reset_trace_store


def _bedrock_frame(payload: dict[str, Any]) -> bytes:
    encoded = base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    return ("\x00\x00binary-prefix" + json.dumps({"bytes": encoded, "p": "abcdefghijk"}) + "\ufffd").encode()


def _bedrock_body() -> bytes:
    return b"".join(
        [
            _bedrock_frame(
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg_1",
                        "type": "message",
                        "role": "assistant",
                        "model": "claude-sonnet-4-6",
                        "content": [],
                        "usage": {"input_tokens": 6, "cache_read_input_tokens": 2, "output_tokens": 0},
                    },
                }
            ),
            _bedrock_frame({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
            _bedrock_frame({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "OK"}}),
            _bedrock_frame({"type": "content_block_stop", "index": 0}),
            _bedrock_frame(
                {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 2}}
            ),
            _bedrock_frame({"type": "message_stop"}),
        ]
    )


def _native_bedrock_eventstream_events(body: bytes) -> list[tuple[dict[str, str], dict[str, Any]]]:
    events: list[tuple[dict[str, str], dict[str, Any]]] = []
    offset = 0
    while offset < len(body):
        total_len, headers_len = struct.unpack("!II", body[offset : offset + 8])
        prelude = body[offset : offset + 8]
        prelude_crc = struct.unpack("!I", body[offset + 8 : offset + 12])[0]
        assert zlib.crc32(prelude) & 0xFFFFFFFF == prelude_crc

        message = body[offset : offset + total_len - 4]
        message_crc = struct.unpack("!I", body[offset + total_len - 4 : offset + total_len])[0]
        assert zlib.crc32(message) & 0xFFFFFFFF == message_crc

        payload_start = offset + 12 + headers_len
        payload_end = offset + total_len - 4
        headers = _native_bedrock_eventstream_headers(body[offset + 12 : payload_start])
        events.append((headers, json.loads(body[payload_start:payload_end])))
        offset += total_len
    return events


def _native_bedrock_eventstream_headers(data: bytes) -> dict[str, str]:
    headers: dict[str, str] = {}
    offset = 0
    while offset < len(data):
        name_len = data[offset]
        offset += 1
        name = data[offset : offset + name_len].decode()
        offset += name_len
        value_type = data[offset]
        offset += 1
        assert value_type == 7
        value_len = struct.unpack("!H", data[offset : offset + 2])[0]
        offset += 2
        headers[name] = data[offset : offset + value_len].decode()
        offset += value_len
    return headers


def _native_bedrock_eventstream_payloads(body: bytes) -> list[dict[str, Any]]:
    return [payload for _headers, payload in _native_bedrock_eventstream_events(body)]


def _bedrock_converse_body() -> bytes:
    return b"".join(
        [
            _bedrock_frame({"messageStart": {"role": "assistant"}}),
            _bedrock_frame({"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "OK"}}}),
            _bedrock_frame({"contentBlockStop": {"contentBlockIndex": 0}}),
            _bedrock_frame({"messageStop": {"stopReason": "end_turn"}}),
            _bedrock_frame(
                {
                    "metadata": {
                        "usage": {
                            "inputTokens": 6,
                            "outputTokens": 2,
                            "totalTokens": 8,
                            "cacheReadInputTokens": 3,
                            "cacheWriteInputTokens": 1,
                        }
                    }
                }
            ),
        ]
    )


def _bedrock_body_with_error() -> bytes:
    return b"".join(
        [
            _bedrock_frame(
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg_1",
                        "type": "message",
                        "role": "assistant",
                        "model": "claude-sonnet-4-6",
                        "content": [],
                        "usage": {"input_tokens": 6},
                    },
                }
            ),
            _bedrock_frame(
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "partial"}}
            ),
            _bedrock_frame({"modelStreamErrorException": {"message": "stream failed", "originalStatusCode": 424}}),
        ]
    )


def _make_writer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, str, TraceWriter]:
    monkeypatch.setenv("CLOUDTAP_DB", str(tmp_path / "traces.sqlite3"))
    reset_trace_store()
    store = get_trace_store()
    session_id = store.create_session(client="claude", proxy_mode="reverse")
    return store, session_id, TraceWriter(session_id, store=store)


async def _start_reverse_proxy(
    target_url: str, writer: TraceWriter, *, store_stream_events: bool = True, capture_only: bool = False
) -> tuple[web.AppRunner, int, aiohttp.ClientSession]:
    session = aiohttp.ClientSession(auto_decompress=False)
    app = web.Application(client_max_size=0)
    app["trace_ctx"] = {
        "target_url": target_url,
        "writer": writer,
        "session": session,
        "turn_counter": 0,
        "store_stream_events": store_stream_events,
        "capture_only": capture_only,
    }
    app.router.add_route("*", "/{path_info:.*}", proxy_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, port, session


@pytest.mark.asyncio
async def test_reverse_proxy_capture_only_records_without_upstream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    async def upstream_handler(_request: web.Request) -> web.Response:
        raise AssertionError("capture-only must not call upstream")

    upstream_app = web.Application()
    upstream_app.router.add_route("*", "/{path_info:.*}", upstream_handler)
    upstream_runner = web.AppRunner(upstream_app)
    await upstream_runner.setup()
    upstream_site = web.TCPSite(upstream_runner, "127.0.0.1", 0)
    await upstream_site.start()
    upstream_port = upstream_site._server.sockets[0].getsockname()[1]

    store, session_id, writer = _make_writer(tmp_path, monkeypatch)
    runner, port, session = await _start_reverse_proxy(
        f"http://127.0.0.1:{upstream_port}",
        writer,
        capture_only=True,
    )

    try:
        async with aiohttp.ClientSession() as client:
            response = await client.post(
                f"http://127.0.0.1:{port}/v1/messages",
                json={
                    "model": "claude-opus",
                    "system": "system prompt",
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )
            assert response.status == 200
            assert (await response.json())["content"][0]["text"] == "captured"
    finally:
        writer.close()
        await runner.cleanup()
        await session.close()
        await upstream_runner.cleanup()

    records = store.load_records(session_id)
    assert len(records) == 1
    assert records[0]["request"]["body"]["system"] == "system prompt"


@pytest.mark.asyncio
async def test_reverse_proxy_capture_only_streams_when_requested(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    async def upstream_handler(_request: web.Request) -> web.Response:
        raise AssertionError("capture-only must not call upstream")

    upstream_app = web.Application()
    upstream_app.router.add_route("*", "/{path_info:.*}", upstream_handler)
    upstream_runner = web.AppRunner(upstream_app)
    await upstream_runner.setup()
    upstream_site = web.TCPSite(upstream_runner, "127.0.0.1", 0)
    await upstream_site.start()
    upstream_port = upstream_site._server.sockets[0].getsockname()[1]

    store, session_id, writer = _make_writer(tmp_path, monkeypatch)
    runner, port, session = await _start_reverse_proxy(
        f"http://127.0.0.1:{upstream_port}",
        writer,
        capture_only=True,
    )

    try:
        async with aiohttp.ClientSession() as client:
            response = await client.post(
                f"http://127.0.0.1:{port}/v1/messages",
                json={
                    "model": "claude-opus",
                    "stream": True,
                    "system": "streaming system prompt",
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )
            assert response.status == 200
            assert response.headers["Content-Type"].startswith("text/event-stream")
            assert "event: message_stop" in await response.text()
    finally:
        writer.close()
        await runner.cleanup()
        await session.close()
        await upstream_runner.cleanup()

    records = store.load_records(session_id)
    assert len(records) == 1
    assert records[0]["response"]["headers"]["Content-Type"] == "text/event-stream"
    assert records[0]["request"]["body"]["system"] == "streaming system prompt"


def test_capture_only_response_shapes_model_probes_by_provider() -> None:
    assert is_capture_only_request("/v1/models/gpt-5", None)
    assert not is_capture_only_request("/oauth/token", {"refresh_token": "redacted"})
    assert not is_capture_only_request("/v1/embeddings", {"input": "embed me", "model": "text-embedding-3-small"})
    assert not is_capture_only_request("/v1internal:listExperiments", {"request": {"client": "agy"}})
    assert is_capture_only_request("/v1internal:streamGenerateContent?alt=sse", {"request": {"contents": []}})

    openai_model = capture_only_response("/v1/models/gpt-5", None)
    assert openai_model == {"id": "gpt-5", "object": "model", "created": 0, "owned_by": "claude-tap"}

    gemini = capture_only_response("/v1beta/models/gemini-pro:generateContent", None)
    assert "candidates" in gemini
    gemini_models = capture_only_response("/v1beta/models", {"model": "gemini-pro"})
    assert gemini_models["models"][0]["name"] == "models/gemini-pro"
    gemini_model = capture_only_response("/v1beta/models/gemini-pro", {"model": "gemini-pro"})
    assert gemini_model["supportedGenerationMethods"] == ["generateContent", "streamGenerateContent"]
    converse = capture_only_response("/model/us.anthropic.claude-sonnet-4-6-v1:0/converse", {"messages": []})
    assert converse["output"]["message"]["content"][0]["text"] == "captured"
    assert converse["stopReason"] == "end_turn"
    anthropic_completion = capture_only_response("/v1/complete", {"model": "claude", "prompt": "hello"})
    assert anthropic_completion["completion"] == "captured"
    completion = capture_only_response("/v1/completions", {"model": "gpt", "prompt": "hello"})
    assert completion["choices"][0]["text"] == "captured"
    responses = capture_only_response("/v1/responses", {"model": "gpt", "input": "hello"})
    assert responses["status"] == "completed"


def test_capture_only_stream_bytes_are_provider_shaped() -> None:
    anthropic = capture_only_stream_bytes("/v1/messages", {"model": "claude"})
    assert b"event: message_start" in anthropic
    assert b'"type":"message_start","message"' in anthropic
    assert b"data: [DONE]" in capture_only_stream_bytes("/v1/chat/completions", {"model": "gpt"})
    assert b"response.completed" in capture_only_stream_bytes("/v1/responses", {"model": "gpt"})
    assert b'"object":"text_completion"' in capture_only_stream_bytes("/v1/completions", {"model": "gpt"})
    gemini_path = "/v1beta/models/gemini-pro:streamGenerateContent?alt=sse"
    assert is_capture_only_streaming_request(gemini_path, {"contents": []})
    assert b"candidates" in capture_only_stream_bytes(gemini_path, {"contents": []})
    code_assist_path = "/v1internal:streamGenerateContent?alt=sse"
    assert b"candidates" in capture_only_stream_bytes(code_assist_path, {"request": {"contents": []}})
    assert b"response.completed" not in capture_only_stream_bytes(code_assist_path, {"request": {"contents": []}})


def test_capture_only_bedrock_stream_bytes_are_eventstream_shaped() -> None:
    path = "/model/global.anthropic.claude-sonnet-4-6-v1/invoke-with-response-stream"

    body = capture_only_stream_bytes(path, {"anthropic_version": "bedrock-2023-05-31"})

    assert capture_only_content_type(path, True) == "application/vnd.amazon.eventstream"
    payloads = _native_bedrock_eventstream_payloads(body)
    assert payloads[0]["type"] == "message_start"
    assert payloads[-1]["type"] == "message_stop"

    converse_path = "/model/us.anthropic.claude-sonnet-4-6-v1:0/converse-stream"
    converse_events = _native_bedrock_eventstream_events(capture_only_stream_bytes(converse_path, {}))
    assert [headers[":event-type"] for headers, _payload in converse_events] == [
        "messageStart",
        "contentBlockDelta",
        "contentBlockStop",
        "messageStop",
        "metadata",
    ]


@pytest.mark.asyncio
async def test_reverse_proxy_capture_only_captures_nested_code_assist_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    async def upstream_handler(_request: web.Request) -> web.Response:
        raise AssertionError("capture-only must not call upstream for Code Assist prompt requests")

    upstream_app = web.Application()
    upstream_app.router.add_route("*", "/{path_info:.*}", upstream_handler)
    upstream_runner = web.AppRunner(upstream_app)
    await upstream_runner.setup()
    upstream_site = web.TCPSite(upstream_runner, "127.0.0.1", 0)
    await upstream_site.start()
    upstream_port = upstream_site._server.sockets[0].getsockname()[1]

    store, session_id, writer = _make_writer(tmp_path, monkeypatch)
    runner, port, session = await _start_reverse_proxy(
        f"http://127.0.0.1:{upstream_port}",
        writer,
        capture_only=True,
    )

    try:
        async with aiohttp.ClientSession() as client:
            response = await client.post(
                f"http://127.0.0.1:{port}/v1internal:streamGenerateContent?alt=sse",
                json={"request": {"contents": [{"role": "user", "parts": [{"text": "hello"}]}]}},
            )
            assert response.status == 200
            assert response.headers["Content-Type"].startswith("text/event-stream")
            assert "candidates" in await response.text()
    finally:
        writer.close()
        await runner.cleanup()
        await session.close()
        await upstream_runner.cleanup()

    records = store.load_records(session_id)
    assert len(records) == 1
    assert records[0]["request"]["path"] == "/v1internal:streamGenerateContent?alt=sse"
    assert records[0]["request"]["body"]["request"]["contents"][0]["role"] == "user"


@pytest.mark.parametrize(
    "bedrock_path",
    [
        "/model/arn:aws:bedrock:us-east-1:123456789012:provisioned-model%2Fabc/invoke-with-response-stream",
        "/model/us.anthropic.claude-sonnet-4-6-v1:0/converse-stream",
    ],
)
@pytest.mark.asyncio
async def test_reverse_proxy_records_bedrock_eventstream_without_stream_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bedrock_path: str,
) -> None:
    bedrock_bytes = _bedrock_converse_body() if "converse-stream" in bedrock_path else _bedrock_body()

    async def upstream_handler(request: web.Request) -> web.StreamResponse:
        assert request.raw_path == bedrock_path
        assert (await request.json())["messages"][0]["role"] == "user"
        response = web.StreamResponse(status=200, headers={"Content-Type": "application/vnd.amazon.eventstream"})
        await response.prepare(request)
        await response.write(bedrock_bytes[:64])
        await response.write(bedrock_bytes[64:])
        await response.write_eof()
        return response

    upstream_app = web.Application()
    upstream_app.router.add_post("/{path_info:.*}", upstream_handler)
    upstream_runner = web.AppRunner(upstream_app)
    await upstream_runner.setup()
    upstream_site = web.TCPSite(upstream_runner, "127.0.0.1", 0)
    await upstream_site.start()
    upstream_port = upstream_site._server.sockets[0].getsockname()[1]

    store, session_id, writer = _make_writer(tmp_path, monkeypatch)
    proxy_runner, proxy_port, proxy_session = await _start_reverse_proxy(f"http://127.0.0.1:{upstream_port}", writer)

    try:
        async with aiohttp.ClientSession(auto_decompress=False) as client:
            async with client.post(
                f"http://127.0.0.1:{proxy_port}{bedrock_path}",
                headers={"X-Amz-Security-Token": "aws-session-token-secret"},
                json={"messages": [{"role": "user", "content": [{"type": "text", "text": "ping"}]}]},
            ) as response:
                assert response.status == 200
                assert await response.read() == bedrock_bytes

        writer.close()
        records = store.load_records(session_id)
        assert len(records) == 1
        record = records[0]
        assert record["request"]["path"] == bedrock_path
        assert record["request"]["headers"]["X-Amz-Security-Token"] == "***"
        if "converse-stream" not in bedrock_path:
            assert record["response"]["body"]["model"] == "claude-sonnet-4-6"
        assert record["response"]["body"]["content"] == [{"type": "text", "text": "OK"}]
        assert record["response"]["body"]["usage"]["input_tokens"] == 6
        assert record["response"]["body"]["usage"]["output_tokens"] == 2
        if "converse-stream" in bedrock_path:
            assert record["response"]["body"]["usage"]["cache_read_input_tokens"] == 3
            assert record["response"]["body"]["usage"]["cache_creation_input_tokens"] == 1
        assert "content_block_delta" in [event["event"] for event in record["response"]["sse_events"]]
    finally:
        await proxy_session.close()
        await proxy_runner.cleanup()
        await upstream_runner.cleanup()
        reset_trace_store()


@pytest.mark.asyncio
async def test_reverse_proxy_preserves_bedrock_stream_error_without_stream_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bedrock_path = "/model/global.anthropic.claude-sonnet-4-6-v1/invoke-with-response-stream"
    bedrock_bytes = _bedrock_body_with_error()

    async def upstream_handler(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(status=200, headers={"Content-Type": "application/vnd.amazon.eventstream"})
        await response.prepare(request)
        await response.write(bedrock_bytes)
        await response.write_eof()
        return response

    upstream_app = web.Application()
    upstream_app.router.add_post("/{path_info:.*}", upstream_handler)
    upstream_runner = web.AppRunner(upstream_app)
    await upstream_runner.setup()
    upstream_site = web.TCPSite(upstream_runner, "127.0.0.1", 0)
    await upstream_site.start()
    upstream_port = upstream_site._server.sockets[0].getsockname()[1]

    store, session_id, writer = _make_writer(tmp_path, monkeypatch)
    proxy_runner, proxy_port, proxy_session = await _start_reverse_proxy(
        f"http://127.0.0.1:{upstream_port}", writer, store_stream_events=False
    )

    try:
        async with aiohttp.ClientSession(auto_decompress=False) as client:
            async with client.post(
                f"http://127.0.0.1:{proxy_port}{bedrock_path}",
                json={"messages": [{"role": "user", "content": [{"type": "text", "text": "ping"}]}]},
            ) as response:
                assert response.status == 200
                assert await response.read() == bedrock_bytes

        writer.close()
        record = store.load_records(session_id)[0]
        body = record["response"]["body"]
        assert "sse_events" not in record["response"]
        assert body["content"] == [{"type": "text", "text": "partial"}]
        assert body["error"]["type"] == "modelStreamErrorException"
        assert body["error"]["message"] == "stream failed"
        assert body["bedrock_errors"] == [
            {"type": "modelStreamErrorException", "message": "stream failed", "originalStatusCode": 424}
        ]
    finally:
        await proxy_session.close()
        await proxy_runner.cleanup()
        await upstream_runner.cleanup()
        reset_trace_store()


class _FakeStreamContent:
    def __init__(self, body: bytes) -> None:
        self._body = body

    async def iter_any(self):
        yield self._body[:80]
        yield self._body[80:]


class _FakeStreamResponse:
    status = 200
    reason = "OK"
    headers = {"Content-Type": "application/vnd.amazon.eventstream"}

    def __init__(self, body: bytes) -> None:
        self.content = _FakeStreamContent(body)


class _FakeSession:
    def __init__(self, body: bytes) -> None:
        self._body = body
        self.calls: list[dict[str, Any]] = []

    async def request(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeStreamResponse(self._body)


class _MemoryWriter:
    def __init__(self) -> None:
        self.data = bytearray()

    def write(self, data: bytes) -> None:
        self.data.extend(data)

    async def drain(self) -> None:
        return None


@pytest.mark.parametrize(
    "bedrock_path",
    [
        "/model/global.anthropic.claude-sonnet-4-6-v1/invoke-with-response-stream",
        "/model/global.anthropic.claude-sonnet-4-6-v1:0/converse-stream",
    ],
)
@pytest.mark.asyncio
async def test_forward_proxy_records_bedrock_eventstream_without_stream_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bedrock_path: str,
) -> None:
    bedrock_bytes = _bedrock_converse_body() if "converse-stream" in bedrock_path else _bedrock_body()
    store, session_id, writer = _make_writer(tmp_path, monkeypatch)
    fake_session = _FakeSession(bedrock_bytes)
    client_writer = _MemoryWriter()
    server = ForwardProxyServer(
        host="127.0.0.1",
        port=0,
        ca=object(),
        writer=writer,
        session=fake_session,
        store_stream_events=True,
    )

    await server._forward_and_record(
        "POST",
        bedrock_path,
        {
            "Host": "bedrock-runtime.us-east-1.amazonaws.com",
            "Authorization": "Bearer test",
            "X-Amz-Security-Token": "aws-session-token-secret",
        },
        json.dumps({"messages": [{"role": "user", "content": [{"type": "text", "text": "ping"}]}]}).encode(),
        f"https://bedrock-runtime.us-east-1.amazonaws.com{bedrock_path}",
        client_writer,
    )

    writer.close()
    records = store.load_records(session_id)
    assert len(records) == 1
    record = records[0]
    assert fake_session.calls[0]["data"]
    assert record["request"]["headers"]["X-Amz-Security-Token"] == "***"
    assert b"Transfer-Encoding: chunked" in client_writer.data
    assert client_writer.data.endswith(b"0\r\n\r\n")
    if "converse-stream" not in bedrock_path:
        assert record["response"]["body"]["model"] == "claude-sonnet-4-6"
    assert record["response"]["body"]["content"] == [{"type": "text", "text": "OK"}]
    if "converse-stream" not in bedrock_path:
        assert record["response"]["body"]["usage"]["cache_read_input_tokens"] == 2
    else:
        assert record["response"]["body"]["usage"]["cache_read_input_tokens"] == 3
        assert record["response"]["body"]["usage"]["cache_creation_input_tokens"] == 1
    assert record["response"]["body"]["usage"]["output_tokens"] == 2
    assert [event["event"] for event in record["response"]["sse_events"]][0] == "message_start"
    reset_trace_store()


@pytest.mark.asyncio
async def test_forward_proxy_preserves_bedrock_stream_error_without_stream_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bedrock_path = "/model/global.anthropic.claude-sonnet-4-6-v1/invoke-with-response-stream"
    store, session_id, writer = _make_writer(tmp_path, monkeypatch)
    fake_session = _FakeSession(_bedrock_body_with_error())
    client_writer = _MemoryWriter()
    server = ForwardProxyServer(
        host="127.0.0.1",
        port=0,
        ca=object(),
        writer=writer,
        session=fake_session,
        store_stream_events=False,
    )

    await server._forward_and_record(
        "POST",
        bedrock_path,
        {"Host": "bedrock-runtime.us-east-1.amazonaws.com", "Authorization": "Bearer test"},
        json.dumps({"messages": [{"role": "user", "content": [{"type": "text", "text": "ping"}]}]}).encode(),
        f"https://bedrock-runtime.us-east-1.amazonaws.com{bedrock_path}",
        client_writer,
    )

    writer.close()
    record = store.load_records(session_id)[0]
    body = record["response"]["body"]
    assert "sse_events" not in record["response"]
    assert body["content"] == [{"type": "text", "text": "partial"}]
    assert body["error"]["type"] == "modelStreamErrorException"
    assert body["error"]["message"] == "stream failed"
    assert body["bedrock_errors"] == [
        {"type": "modelStreamErrorException", "message": "stream failed", "originalStatusCode": 424}
    ]
    reset_trace_store()
