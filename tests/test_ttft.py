"""Tests for TTFT (time-to-first-token) capture on streaming proxy calls.

Streaming records must carry ``ttft_ms`` — the wall-clock delta between
sending the upstream request and receiving the first upstream byte — so
consumers can split connection/TTFB latency from generation time.
Non-streaming records must not carry the key.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from aiohttp import web

from claude_tap.forward_proxy import ForwardProxyServer
from claude_tap.proxy import _build_record, proxy_handler
from claude_tap.trace import TraceWriter
from claude_tap.trace_store import get_trace_store, reset_trace_store

STREAM_DELAY_S = 0.3
MIN_TTFT_TO_TOTAL_GAP_MS = 100


def _anthropic_stream_frames() -> bytes:
    return b"".join(
        [
            b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_1","type":"message","role":"assistant","model":"claude-sonnet-4-6","content":[],"usage":{"input_tokens":6,"output_tokens":1}}}\n\n',
            b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hi"}}\n\n',
            b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
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
        "capture_only": False,
    }
    app.router.add_route("*", "/{path_info:.*}", proxy_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, port, session


async def _start_streaming_upstream(frames_before_delay: bytes, frames_after_delay: bytes, delay_s: float):
    async def upstream_handler(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(frames_before_delay)
        await asyncio.sleep(delay_s)
        await response.write(frames_after_delay)
        await response.write_eof()
        return response

    app = web.Application()
    app.router.add_post("/{path_info:.*}", upstream_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, port


@pytest.mark.asyncio
async def test_reverse_proxy_streaming_record_contains_ttft(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TTFT is stamped at the first upstream byte, well before the stream ends."""
    all_frames = _anthropic_stream_frames()
    split = len(all_frames) // 2
    upstream_runner, upstream_port = await _start_streaming_upstream(
        all_frames[:split], all_frames[split:], STREAM_DELAY_S
    )

    store, session_id, writer = _make_writer(tmp_path, monkeypatch)
    proxy_runner, proxy_port, proxy_session = await _start_reverse_proxy(f"http://127.0.0.1:{upstream_port}", writer)

    try:
        async with aiohttp.ClientSession(auto_decompress=False) as client:
            async with client.post(
                f"http://127.0.0.1:{proxy_port}/v1/messages",
                json={"model": "claude-sonnet-4-6", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
            ) as response:
                assert response.status == 200
                assert await response.read() == all_frames

        writer.close()
        records = store.load_records(session_id)
        assert len(records) == 1
        record = records[0]
        assert "ttft_ms" in record
        assert record["ttft_ms"] >= 0
        assert record["ttft_ms"] <= record["duration_ms"]
        # The upstream stalls STREAM_DELAY_S between chunks, so completion must
        # trail the first byte by a wide margin. If TTFT were captured at the
        # end of the stream instead, ttft_ms would equal duration_ms.
        assert record["duration_ms"] - record["ttft_ms"] >= MIN_TTFT_TO_TOTAL_GAP_MS
    finally:
        await proxy_session.close()
        await proxy_runner.cleanup()
        await upstream_runner.cleanup()
        reset_trace_store()


@pytest.mark.asyncio
async def test_reverse_proxy_forwards_headers_before_first_upstream_byte(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slow first token must not delay the upstream status and headers.

    The upstream sends its 200 headers immediately but stalls before the first
    body byte. The client must receive the response headers right away (so
    short response-header timeouts survive), while the record's ttft_ms still
    measures the full first-byte latency.
    """
    all_frames = _anthropic_stream_frames()
    upstream_runner, upstream_port = await _start_streaming_upstream(b"", all_frames, STREAM_DELAY_S)

    store, session_id, writer = _make_writer(tmp_path, monkeypatch)
    proxy_runner, proxy_port, proxy_session = await _start_reverse_proxy(f"http://127.0.0.1:{upstream_port}", writer)

    try:
        async with aiohttp.ClientSession(auto_decompress=False) as client:
            request_start = time.monotonic()
            async with client.post(
                f"http://127.0.0.1:{proxy_port}/v1/messages",
                json={"model": "claude-sonnet-4-6", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
            ) as response:
                header_wait_ms = (time.monotonic() - request_start) * 1000
                assert response.status == 200
                assert await response.read() == all_frames

        writer.close()
        records = store.load_records(session_id)
        assert len(records) == 1
        record = records[0]
        assert header_wait_ms < STREAM_DELAY_S * 500
        assert record["ttft_ms"] >= STREAM_DELAY_S * 1000 * 0.8
        assert record["ttft_ms"] <= record["duration_ms"]
    finally:
        await proxy_session.close()
        await proxy_runner.cleanup()
        await upstream_runner.cleanup()
        reset_trace_store()


@pytest.mark.asyncio
async def test_reverse_proxy_non_streaming_record_has_no_ttft(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-streaming responses have a single payload, so there is no TTFT to report."""

    async def upstream_handler(_request: web.Request) -> web.Response:
        return web.json_response({"id": "msg_1", "role": "assistant", "content": [], "usage": {"input_tokens": 1}})

    app = web.Application()
    app.router.add_post("/{path_info:.*}", upstream_handler)
    upstream_runner = web.AppRunner(app)
    await upstream_runner.setup()
    upstream_site = web.TCPSite(upstream_runner, "127.0.0.1", 0)
    await upstream_site.start()
    upstream_port = upstream_site._server.sockets[0].getsockname()[1]

    store, session_id, writer = _make_writer(tmp_path, monkeypatch)
    proxy_runner, proxy_port, proxy_session = await _start_reverse_proxy(f"http://127.0.0.1:{upstream_port}", writer)

    try:
        async with aiohttp.ClientSession() as client:
            async with client.post(
                f"http://127.0.0.1:{proxy_port}/v1/messages",
                json={"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "hi"}]},
            ) as response:
                assert response.status == 200
                assert await response.read()

        writer.close()
        records = store.load_records(session_id)
        assert len(records) == 1
        assert "ttft_ms" not in records[0]
    finally:
        await proxy_session.close()
        await proxy_runner.cleanup()
        await upstream_runner.cleanup()
        reset_trace_store()


class _FakeStreamContent:
    def __init__(
        self, body: bytes, events: list[tuple[str, bytes]] | None = None, readany_delay_s: float = 0.0
    ) -> None:
        self._body = body
        self._offset = 0
        self._events = events
        self._readany_delay_s = readany_delay_s

    async def readany(self) -> bytes:
        if self._readany_delay_s:
            await asyncio.sleep(self._readany_delay_s)
        if self._events is not None:
            self._events.append(("readany", b""))
        if self._offset >= len(self._body):
            return b""
        chunk = self._body[self._offset : self._offset + len(self._body) // 2]
        self._offset += len(chunk)
        return chunk

    async def iter_any(self):
        while self._offset < len(self._body):
            chunk = self._body[self._offset : self._offset + 64]
            self._offset += len(chunk)
            yield chunk


class _FakeStreamResponse:
    status = 200
    reason = "OK"
    headers = {"Content-Type": "text/event-stream"}

    def __init__(
        self, body: bytes, events: list[tuple[str, bytes]] | None = None, readany_delay_s: float = 0.0
    ) -> None:
        self.content = _FakeStreamContent(body, events=events, readany_delay_s=readany_delay_s)


class _FakeSession:
    def __init__(
        self, body: bytes, events: list[tuple[str, bytes]] | None = None, readany_delay_s: float = 0.0
    ) -> None:
        self._body = body
        self._events = events
        self._readany_delay_s = readany_delay_s
        self.calls: list[dict[str, Any]] = []

    async def request(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeStreamResponse(self._body, events=self._events, readany_delay_s=self._readany_delay_s)


class _MemoryWriter:
    def __init__(self, events: list[tuple[str, bytes]] | None = None) -> None:
        self.data = bytearray()
        self._events = events

    def write(self, data: bytes) -> None:
        if self._events is not None:
            self._events.append(("write", bytes(data)))
        self.data.extend(data)

    async def drain(self) -> None:
        return None


@pytest.mark.asyncio
async def test_forward_proxy_streaming_record_contains_ttft(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The TLS MITM forward proxy stamps ttft_ms the same way as the reverse proxy."""
    frames = _anthropic_stream_frames()
    store, session_id, writer = _make_writer(tmp_path, monkeypatch)
    fake_session = _FakeSession(frames)
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
        "/v1/messages",
        {"Host": "api.anthropic.com", "Authorization": "Bearer test"},
        json.dumps(
            {
                "model": "claude-sonnet-4-6",
                "stream": True,
                "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            }
        ).encode(),
        "https://api.anthropic.com/v1/messages",
        client_writer,
    )

    writer.close()
    records = store.load_records(session_id)
    assert len(records) == 1
    record = records[0]
    assert "ttft_ms" in record
    assert record["ttft_ms"] >= 0
    assert record["ttft_ms"] <= record["duration_ms"]
    reset_trace_store()


@pytest.mark.asyncio
async def test_forward_proxy_ttft_timestamped_before_first_body_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TTFT is measured at the upstream I/O boundary without withholding headers.

    The status line and response headers are forwarded while the first upstream
    read is still in flight, and ttft_ms is stamped when that read completes,
    before the first body write. The fake upstream delays readany by 50ms: the
    event log must show header writes before readany completes, and the first
    body write only after it, with ttft_ms including the 50ms delay.
    """
    events: list[tuple[str, bytes]] = []
    frames = _anthropic_stream_frames()
    store, session_id, writer = _make_writer(tmp_path, monkeypatch)
    fake_session = _FakeSession(frames, events=events, readany_delay_s=0.05)
    client_writer = _MemoryWriter(events=events)
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
        "/v1/messages",
        {"Host": "api.anthropic.com", "Authorization": "Bearer test"},
        json.dumps(
            {
                "model": "claude-sonnet-4-6",
                "stream": True,
                "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            }
        ).encode(),
        "https://api.anthropic.com/v1/messages",
        client_writer,
    )

    writer.close()
    records = store.load_records(session_id)
    assert len(records) == 1
    record = records[0]

    first_body_idx = next(i for i, (_, data) in enumerate(events) if b"message_start" in data)
    readany_idx = next(i for i, (kind, _) in enumerate(events) if kind == "readany")
    # Headers go out while the first upstream read is still in flight...
    assert all(kind == "write" for kind, _ in events[:readany_idx])
    assert events[0][1].startswith(b"HTTP/1.1 200 OK\r\n")
    # ...and the first body write happens only after ttft_ms was stamped.
    assert readany_idx < first_body_idx
    assert record["ttft_ms"] >= 40
    assert record["ttft_ms"] <= record["duration_ms"]
    reset_trace_store()


def test_build_record_ttft_ms_is_optional() -> None:
    """_build_record only adds ttft_ms when a value was measured."""
    common = (
        "req-1",
        1,
        500,
        "POST",
        "/v1/messages",
        {},
        None,
        200,
        {},
        {"role": "assistant"},
    )
    without = _build_record(*common)
    assert "ttft_ms" not in without

    with_ttft = _build_record(*common, ttft_ms=120)
    assert with_ttft["ttft_ms"] == 120
