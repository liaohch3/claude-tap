from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable

import aiohttp
import pytest
from aiohttp import web

from claude_tap.forward_proxy import ForwardProxyServer


class _MemoryTraceWriter:
    def __init__(self) -> None:
        self.records: list[dict] = []

    async def write(self, record: dict) -> None:
        self.records.append(record)


class _MemoryClientWriter:
    def __init__(self, first_payload: bytes | None = None) -> None:
        self.buffer = bytearray()
        self._first_payload = first_payload
        self.first_payload_forwarded = asyncio.Event()

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)
        if self._first_payload and self._first_payload in self.buffer:
            self.first_payload_forwarded.set()

    async def drain(self) -> None:
        return None


class _DisconnectingClientWriter(_MemoryClientWriter):
    def __init__(self) -> None:
        super().__init__()
        self.drain_calls = 0

    async def drain(self) -> None:
        self.drain_calls += 1
        if self.drain_calls > 1:
            raise ConnectionError("client disconnected")


async def _start_http_upstream(
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> tuple[str, web.AppRunner]:
    app = web.Application()
    app.router.add_route("*", "/{path_info:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return f"http://127.0.0.1:{port}", runner


@pytest.mark.asyncio
async def test_forward_proxy_streams_event_stream_response_before_upstream_closes() -> None:
    release_upstream = asyncio.Event()
    upstream_sent_first = asyncio.Event()

    async def handler(request: web.Request) -> web.StreamResponse:
        assert await request.json() == {"jsonrpc": "2.0", "method": "tools/call"}
        response = web.StreamResponse(
            status=200,
            headers={"Content-Type": "Text/Event-Stream; Charset=UTF-8"},
        )
        await response.prepare(request)
        await response.write(b'event: mcp.progress\ndata: {"step":1}\n\n')
        upstream_sent_first.set()
        await release_upstream.wait()
        await response.write(b'event: mcp.result\ndata: {"ok":true}\n\n')
        await response.write_eof()
        return response

    upstream, runner = await _start_http_upstream(handler)
    trace_writer = _MemoryTraceWriter()
    client_writer = _MemoryClientWriter(b'event: mcp.progress\ndata: {"step":1}')

    async with aiohttp.ClientSession(auto_decompress=False) as session:
        server = ForwardProxyServer(
            host="127.0.0.1",
            port=0,
            ca=object(),
            writer=trace_writer,
            session=session,
            store_stream_events=True,
        )
        task = asyncio.create_task(
            server._forward_and_record(
                "POST",
                "/mcp",
                {"Content-Type": "application/json"},
                b'{"jsonrpc":"2.0","method":"tools/call"}',
                f"{upstream}/mcp",
                client_writer,
            )
        )
        try:
            await asyncio.wait_for(upstream_sent_first.wait(), timeout=1)
            await asyncio.wait_for(client_writer.first_payload_forwarded.wait(), timeout=1)
            assert not task.done()
        finally:
            release_upstream.set()
            await asyncio.wait_for(task, timeout=2)
            await runner.cleanup()

    assert bytes(client_writer.buffer).index(b"mcp.progress") < bytes(client_writer.buffer).index(b"mcp.result")
    assert [event["event"] for event in trace_writer.records[0]["response"]["sse_events"]] == [
        "mcp.progress",
        "mcp.result",
    ]


@pytest.mark.asyncio
async def test_forward_proxy_keeps_json_response_on_buffered_path() -> None:
    async def handler(_request: web.Request) -> web.StreamResponse:
        return web.json_response({"ok": True, "kind": "json"})

    upstream, runner = await _start_http_upstream(handler)
    trace_writer = _MemoryTraceWriter()
    client_writer = _MemoryClientWriter()

    async with aiohttp.ClientSession(auto_decompress=False) as session:
        server = ForwardProxyServer(
            host="127.0.0.1",
            port=0,
            ca=object(),
            writer=trace_writer,
            session=session,
            store_stream_events=True,
        )
        await server._forward_and_record(
            "POST",
            "/mcp",
            {"Content-Type": "application/json"},
            b'{"jsonrpc":"2.0","method":"initialize"}',
            f"{upstream}/mcp",
            client_writer,
        )
    await runner.cleanup()

    assert trace_writer.records[0]["response"]["body"] == {"ok": True, "kind": "json"}
    assert "sse_events" not in trace_writer.records[0]["response"]
    assert b"Content-Length:" in client_writer.buffer
    assert b"Transfer-Encoding: chunked" not in client_writer.buffer


@pytest.mark.asyncio
async def test_response_driven_sse_closes_upstream_after_client_disconnect() -> None:
    class FakeContent:
        async def iter_any(self):
            yield b'event: mcp.progress\ndata: {"step":1}\n\n'
            await asyncio.Event().wait()

    class FakeResponse:
        status = 200
        reason = "OK"
        headers = {"Content-Type": "text/event-stream", "Content-Length": "42"}
        content = FakeContent()

        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class FakeSession:
        def __init__(self, response: FakeResponse) -> None:
            self.response = response

        async def request(self, **_kwargs: object) -> FakeResponse:
            return self.response

    response = FakeResponse()
    trace_writer = _MemoryTraceWriter()
    client_writer = _DisconnectingClientWriter()
    server = ForwardProxyServer(
        host="127.0.0.1",
        port=0,
        ca=object(),
        writer=trace_writer,
        session=FakeSession(response),
        store_stream_events=True,
    )

    await asyncio.wait_for(
        server._forward_and_record(
            "POST",
            "/mcp",
            {"Content-Type": "application/json"},
            b'{"jsonrpc":"2.0","method":"tools/call"}',
            "https://mcp.example.test/mcp",
            client_writer,
        ),
        timeout=1,
    )

    assert response.closed is True
    assert b"Content-Length:" not in client_writer.buffer
    assert b"0\r\n\r\n" not in client_writer.buffer
    assert len(trace_writer.records) == 1


@pytest.mark.asyncio
async def test_response_driven_sse_cancellation_closes_upstream() -> None:
    iteration_started = asyncio.Event()

    class FakeContent:
        async def iter_any(self):
            iteration_started.set()
            await asyncio.Event().wait()
            yield b"unreachable"

    class FakeResponse:
        status = 200
        reason = "OK"
        headers = {"Content-Type": "text/event-stream"}
        content = FakeContent()

        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class FakeSession:
        def __init__(self, response: FakeResponse) -> None:
            self.response = response

        async def request(self, **_kwargs: object) -> FakeResponse:
            return self.response

    response = FakeResponse()
    server = ForwardProxyServer(
        host="127.0.0.1",
        port=0,
        ca=object(),
        writer=_MemoryTraceWriter(),
        session=FakeSession(response),
        store_stream_events=True,
    )
    task = asyncio.create_task(
        server._forward_and_record(
            "POST",
            "/mcp",
            {"Content-Type": "application/json"},
            b'{"jsonrpc":"2.0","method":"tools/call"}',
            "https://mcp.example.test/mcp",
            _MemoryClientWriter(),
        )
    )

    await asyncio.wait_for(iteration_started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert response.closed is True


@pytest.mark.asyncio
async def test_unfiltered_forward_proxy_records_astron_paths_across_origins() -> None:
    async def model_handler(request: web.Request) -> web.StreamResponse:
        if request.path == "/responses":
            response = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            await response.write(
                b'event: response.completed\ndata: {"response":{"id":"resp_test","status":"completed","output":[]}}\n\n'
            )
            await response.write_eof()
            return response
        return web.json_response({"kind": "models"})

    async def product_handler(request: web.Request) -> web.StreamResponse:
        body = await request.json() if request.can_read_body else {}
        method = body.get("method")
        if method == "tools/call":
            response = web.StreamResponse(
                status=200,
                headers={"Content-Type": "text/event-stream; charset=utf-8"},
            )
            await response.prepare(request)
            await response.write(b'event: mcp.result\ndata: {"ok":true}\n\n')
            await response.write_eof()
            return response
        return web.json_response({"kind": method or request.path.strip("/")})

    model_origin, model_runner = await _start_http_upstream(model_handler)
    product_origin, product_runner = await _start_http_upstream(product_handler)
    trace_writer = _MemoryTraceWriter()

    requests = [
        ("POST", "/responses", {"model": "test", "stream": True}, model_origin),
        ("GET", "/models", None, model_origin),
        ("GET", "/apps", None, product_origin),
        ("POST", "/mcp", {"jsonrpc": "2.0", "method": "initialize"}, product_origin),
        ("POST", "/mcp", {"jsonrpc": "2.0", "method": "tools/list"}, product_origin),
        ("POST", "/mcp", {"jsonrpc": "2.0", "method": "tools/call"}, product_origin),
        ("GET", "/health", None, product_origin),
    ]

    async with aiohttp.ClientSession(auto_decompress=False) as session:
        server = ForwardProxyServer(
            host="127.0.0.1",
            port=0,
            ca=object(),
            writer=trace_writer,
            session=session,
            store_stream_events=True,
        )
        for method, path, body, origin in requests:
            raw_body = json.dumps(body).encode() if body is not None else b""
            headers = {"Content-Type": "application/json"} if body is not None else {}
            await server._forward_and_record(
                method,
                path,
                headers,
                raw_body,
                f"{origin}{path}",
                _MemoryClientWriter(),
            )

    await model_runner.cleanup()
    await product_runner.cleanup()

    assert [record["request"]["path"] for record in trace_writer.records] == [request[1] for request in requests]
    assert {record["upstream_base_url"] for record in trace_writer.records} == {model_origin, product_origin}
    assert all(record["response"]["status"] == 200 for record in trace_writer.records)
    tools_call = trace_writer.records[5]
    assert tools_call["request"]["body"]["method"] == "tools/call"
    assert tools_call["response"]["sse_events"] == [{"event": "mcp.result", "data": {"ok": True}}]
