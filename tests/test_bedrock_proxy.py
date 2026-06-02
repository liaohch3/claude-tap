"""Tests for Bedrock EventStream capture in reverse and forward proxy modes."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from aiohttp import web

from claude_tap.forward_proxy import ForwardProxyServer
from claude_tap.proxy import proxy_handler
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


def _make_writer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, str, TraceWriter]:
    monkeypatch.setenv("CLOUDTAP_DB", str(tmp_path / "traces.sqlite3"))
    reset_trace_store()
    store = get_trace_store()
    session_id = store.create_session(client="claude", proxy_mode="reverse")
    return store, session_id, TraceWriter(session_id, store=store)


async def _start_reverse_proxy(
    target_url: str, writer: TraceWriter
) -> tuple[web.AppRunner, int, aiohttp.ClientSession]:
    session = aiohttp.ClientSession(auto_decompress=False)
    app = web.Application(client_max_size=0)
    app["trace_ctx"] = {
        "target_url": target_url,
        "writer": writer,
        "session": session,
        "turn_counter": 0,
        "store_stream_events": True,
    }
    app.router.add_route("*", "/{path_info:.*}", proxy_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, port, session


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
    bedrock_bytes = _bedrock_body()

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
                json={"messages": [{"role": "user", "content": [{"type": "text", "text": "ping"}]}]},
            ) as response:
                assert response.status == 200
                assert await response.read() == bedrock_bytes

        writer.close()
        records = store.load_records(session_id)
        assert len(records) == 1
        record = records[0]
        assert record["request"]["path"] == bedrock_path
        assert record["response"]["body"]["model"] == "claude-sonnet-4-6"
        assert record["response"]["body"]["content"] == [{"type": "text", "text": "OK"}]
        assert record["response"]["body"]["usage"]["input_tokens"] == 6
        assert record["response"]["body"]["usage"]["output_tokens"] == 2
        assert [event["event"] for event in record["response"]["sse_events"]][-1] == "message_stop"
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
    bedrock_bytes = _bedrock_body()
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
        {"Host": "bedrock-runtime.us-east-1.amazonaws.com", "Authorization": "Bearer test"},
        json.dumps({"messages": [{"role": "user", "content": [{"type": "text", "text": "ping"}]}]}).encode(),
        f"https://bedrock-runtime.us-east-1.amazonaws.com{bedrock_path}",
        client_writer,
    )

    writer.close()
    records = store.load_records(session_id)
    assert len(records) == 1
    record = records[0]
    assert fake_session.calls[0]["data"]
    assert b"Transfer-Encoding: chunked" in client_writer.data
    assert client_writer.data.endswith(b"0\r\n\r\n")
    assert record["response"]["body"]["model"] == "claude-sonnet-4-6"
    assert record["response"]["body"]["content"] == [{"type": "text", "text": "OK"}]
    assert record["response"]["body"]["usage"]["cache_read_input_tokens"] == 2
    assert record["response"]["body"]["usage"]["output_tokens"] == 2
    assert [event["event"] for event in record["response"]["sse_events"]][0] == "message_start"
    reset_trace_store()
