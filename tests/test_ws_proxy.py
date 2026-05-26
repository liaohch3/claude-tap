"""Tests for WebSocket proxy support in reverse proxy mode."""

import asyncio
import json
import os
import shutil
import tempfile
import tracemalloc
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web
from yarl import URL

from claude_tap.proxy import proxy_handler
from claude_tap.trace import TraceWriter
from claude_tap.trace_store import get_trace_store, reset_trace_store
from claude_tap.ws_proxy import _build_ws_record, _get_ws_proxy_settings


@pytest.fixture
def trace_dir():
    d = tempfile.mkdtemp(prefix="claude_tap_ws_test_")
    os.environ["CLOUDTAP_DB"] = str(Path(d) / "ws-test.sqlite3")
    reset_trace_store()
    yield d
    shutil.rmtree(d, ignore_errors=True)
    reset_trace_store()


def _make_writer() -> tuple[object, str, TraceWriter]:
    store = get_trace_store()
    session_id = store.create_session()
    return store, session_id, TraceWriter(session_id, store=store)


def _load_records(store, session_id: str) -> list[dict]:
    return store.load_records(session_id)


async def _start_ws_upstream(handler) -> tuple[web.AppRunner, int]:
    """Start a fake WebSocket upstream server, return (runner, port)."""
    app = web.Application()
    app.router.add_route("*", "/{path:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, port


async def _start_proxy(target_url, writer, strip_prefix="") -> tuple[web.AppRunner, int, aiohttp.ClientSession]:
    """Start the reverse proxy, return (runner, port, session)."""
    session = aiohttp.ClientSession(auto_decompress=False, trust_env=True)
    app = web.Application(client_max_size=0)
    app["trace_ctx"] = {
        "target_url": target_url,
        "writer": writer,
        "session": session,
        "turn_counter": 0,
        "strip_path_prefix": strip_prefix,
    }
    app.router.add_route("*", "/{path_info:.*}", proxy_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, port, session


# ---------------------------------------------------------------------------
# Test 1: basic WebSocket relay and trace recording
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_websocket_proxy_basic(trace_dir):
    """A WebSocket connection through the proxy relays messages and writes a trace."""

    async def ws_upstream_handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)
                model = data.get("model", "test-model")
                await ws.send_json(
                    {
                        "type": "response.created",
                        "response": {"id": "resp_1", "model": model, "status": "in_progress"},
                    }
                )
                await ws.send_json({"type": "response.output_text.delta", "delta": "Hello "})
                await ws.send_json({"type": "response.output_text.delta", "delta": "World"})
                await ws.send_json(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp_1",
                            "model": model,
                            "status": "completed",
                            "output": [
                                {
                                    "type": "message",
                                    "content": [{"type": "output_text", "text": "Hello World"}],
                                }
                            ],
                            "usage": {"input_tokens": 10, "output_tokens": 5},
                        },
                    }
                )
                await ws.close()
                break
        return ws

    store, session_id, writer = _make_writer()

    upstream_runner, upstream_port = await _start_ws_upstream(ws_upstream_handler)
    proxy_runner, proxy_port, proxy_session = await _start_proxy(
        f"http://127.0.0.1:{upstream_port}",
        writer,
        strip_prefix="/v1",
    )

    try:
        async with aiohttp.ClientSession() as client:
            ws = await client.ws_connect(f"http://127.0.0.1:{proxy_port}/v1/responses")
            await ws.send_json({"model": "gpt-test", "input": "hello"})

            received = []
            while True:
                msg = await asyncio.wait_for(ws.receive(), timeout=5)
                if msg.type == aiohttp.WSMsgType.TEXT:
                    received.append(json.loads(msg.data))
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.CLOSED,
                ):
                    break
            await ws.close()

        # Verify relayed messages
        assert len(received) == 4
        assert received[0]["type"] == "response.created"
        assert received[1]["type"] == "response.output_text.delta"
        assert received[3]["type"] == "response.completed"
        assert received[3]["response"]["usage"]["output_tokens"] == 5

        # Allow trace writer to flush
        await asyncio.sleep(0.1)
        writer.close()

        records = store.load_records(session_id)
        assert len(records) == 1
        r = records[0]
        assert r["transport"] == "websocket"
        assert r["request"]["method"] == "WEBSOCKET"
        assert r["request"]["path"] == "/v1/responses"
        assert r["request"]["body"]["model"] == "gpt-test"
        assert r["request"]["ws_events"][0]["model"] == "gpt-test"
        assert r["request"]["ws_events"][0]["input"] == "hello"
        assert r["response"]["status"] == 101
        assert len(r["response"]["ws_events"]) == 4
        assert r["response"]["body"]["status"] == "completed"
        assert r["upstream_base_url"] == f"http://127.0.0.1:{upstream_port}"

    finally:
        await proxy_session.close()
        await proxy_runner.cleanup()
        await upstream_runner.cleanup()


@pytest.mark.asyncio
async def test_websocket_completed_response_is_written_before_socket_close(trace_dir):
    """A completed WS response should be visible before a long-lived socket closes."""
    allow_close = asyncio.Event()

    async def ws_upstream_handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)
                model = data.get("model", "test-model")
                await ws.send_json(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp_live",
                            "model": model,
                            "status": "completed",
                            "output": [{"type": "message", "content": [{"type": "output_text", "text": "done"}]}],
                            "usage": {"input_tokens": 3, "output_tokens": 1},
                        },
                    }
                )
                await allow_close.wait()
                await ws.close()
                break
        return ws

    store, session_id, writer = _make_writer()

    upstream_runner, upstream_port = await _start_ws_upstream(ws_upstream_handler)
    proxy_runner, proxy_port, proxy_session = await _start_proxy(
        f"http://127.0.0.1:{upstream_port}",
        writer,
        strip_prefix="/v1",
    )

    try:
        async with aiohttp.ClientSession() as client:
            ws = await client.ws_connect(f"http://127.0.0.1:{proxy_port}/v1/responses")
            await ws.send_json({"model": "gpt-test", "input": "hello"})

            msg = await asyncio.wait_for(ws.receive(), timeout=5)
            assert msg.type == aiohttp.WSMsgType.TEXT
            assert json.loads(msg.data)["type"] == "response.completed"

            await asyncio.sleep(0.1)
            records = store.load_records(session_id)
            assert len(records) == 1
            assert records[0]["response"]["body"]["status"] == "completed"
            assert records[0]["request"]["body"]["model"] == "gpt-test"

            allow_close.set()
            await ws.close()

        await asyncio.sleep(0.1)
        writer.close()

        records = store.load_records(session_id)
        assert len(records) == 1

    finally:
        allow_close.set()
        await proxy_session.close()
        await proxy_runner.cleanup()
        await upstream_runner.cleanup()


@pytest.mark.asyncio
async def test_websocket_slow_writer_does_not_clear_next_request(trace_dir):
    """New client messages arriving during a slow trace write belong to the next record."""

    class SlowFirstWrite:
        def __init__(self, inner: TraceWriter):
            self.inner = inner
            self.first_write_started = asyncio.Event()
            self.release_first_write = asyncio.Event()
            self.write_count = 0

        async def write(self, record):
            self.write_count += 1
            if self.write_count == 1:
                self.first_write_started.set()
                await self.release_first_write.wait()
            await self.inner.write(record)

        def close(self):
            self.inner.close()

    async def ws_upstream_handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            data = json.loads(msg.data)
            response_id = data["response_id"]
            await ws.send_json(
                {
                    "type": "response.completed",
                    "response": {
                        "id": response_id,
                        "model": data.get("model", "test-model"),
                        "status": "completed",
                        "output": [{"type": "message", "content": [{"type": "output_text", "text": response_id}]}],
                        "usage": {"input_tokens": 3, "output_tokens": 1},
                    },
                }
            )
        return ws

    store, session_id, inner_writer = _make_writer()
    writer = SlowFirstWrite(inner_writer)

    upstream_runner, upstream_port = await _start_ws_upstream(ws_upstream_handler)
    proxy_runner, proxy_port, proxy_session = await _start_proxy(
        f"http://127.0.0.1:{upstream_port}",
        writer,
        strip_prefix="/v1",
    )

    try:
        async with aiohttp.ClientSession() as client:
            ws = await client.ws_connect(f"http://127.0.0.1:{proxy_port}/v1/responses")
            await ws.send_json({"model": "gpt-test", "response_id": "resp1", "input": "first"})

            msg = await asyncio.wait_for(ws.receive(), timeout=5)
            assert msg.type == aiohttp.WSMsgType.TEXT
            assert json.loads(msg.data)["response"]["id"] == "resp1"
            await asyncio.wait_for(writer.first_write_started.wait(), timeout=5)

            await ws.send_json({"model": "gpt-test", "response_id": "resp2", "input": "second"})
            await asyncio.sleep(0.05)
            writer.release_first_write.set()

            msg = await asyncio.wait_for(ws.receive(), timeout=5)
            assert msg.type == aiohttp.WSMsgType.TEXT
            assert json.loads(msg.data)["response"]["id"] == "resp2"
            await ws.close()

        await asyncio.sleep(0.1)
        writer.close()

        records = _load_records(store, session_id)
        assert len(records) == 2
        assert records[0]["request"]["body"]["response_id"] == "resp1"
        assert records[0]["response"]["body"]["id"] == "resp1"
        assert records[1]["request"]["body"]["response_id"] == "resp2"
        assert records[1]["response"]["body"]["id"] == "resp2"

    finally:
        writer.release_first_write.set()
        await proxy_session.close()
        await proxy_runner.cleanup()
        await upstream_runner.cleanup()


@pytest.mark.asyncio
async def test_websocket_completed_snapshot_before_client_send_returns(trace_dir, monkeypatch):
    """A next request sent while completed is still being relayed must not join the old record."""
    first_completed_sent = asyncio.Event()
    release_first_send = asyncio.Event()
    original_send_str = web.WebSocketResponse.send_str

    async def delayed_send_str(self, data, *args, **kwargs):
        result = await original_send_str(self, data, *args, **kwargs)
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            return result
        if parsed.get("type") == "response.completed" and (parsed.get("response") or {}).get("id") == "resp1":
            first_completed_sent.set()
            await release_first_send.wait()
        return result

    monkeypatch.setattr(web.WebSocketResponse, "send_str", delayed_send_str)

    async def ws_upstream_handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            data = json.loads(msg.data)
            response_id = data["response_id"]
            await ws.send_json(
                {
                    "type": "response.completed",
                    "response": {
                        "id": response_id,
                        "model": data.get("model", "test-model"),
                        "status": "completed",
                        "output": [{"type": "message", "content": [{"type": "output_text", "text": response_id}]}],
                        "usage": {"input_tokens": 3, "output_tokens": 1},
                    },
                }
            )
        return ws

    store, session_id, writer = _make_writer()

    upstream_runner, upstream_port = await _start_ws_upstream(ws_upstream_handler)
    proxy_runner, proxy_port, proxy_session = await _start_proxy(
        f"http://127.0.0.1:{upstream_port}",
        writer,
        strip_prefix="/v1",
    )

    try:
        async with aiohttp.ClientSession() as client:
            ws = await client.ws_connect(f"http://127.0.0.1:{proxy_port}/v1/responses")
            await ws.send_json({"model": "gpt-test", "response_id": "resp1", "input": "first"})

            msg = await asyncio.wait_for(ws.receive(), timeout=5)
            assert msg.type == aiohttp.WSMsgType.TEXT
            assert json.loads(msg.data)["response"]["id"] == "resp1"
            await asyncio.wait_for(first_completed_sent.wait(), timeout=5)

            await ws.send_json({"model": "gpt-test", "response_id": "resp2", "input": "second"})
            await asyncio.sleep(0.05)
            release_first_send.set()

            msg = await asyncio.wait_for(ws.receive(), timeout=5)
            assert msg.type == aiohttp.WSMsgType.TEXT
            assert json.loads(msg.data)["response"]["id"] == "resp2"
            await ws.close()

        await asyncio.sleep(0.1)
        writer.close()

        records = _load_records(store, session_id)
        assert len(records) == 2
        assert records[0]["request"]["body"]["response_id"] == "resp1"
        assert records[0]["response"]["body"]["id"] == "resp1"
        assert records[1]["request"]["body"]["response_id"] == "resp2"
        assert records[1]["response"]["body"]["id"] == "resp2"

    finally:
        release_first_send.set()
        await proxy_session.close()
        await proxy_runner.cleanup()
        await upstream_runner.cleanup()


@pytest.mark.asyncio
async def test_websocket_completed_snapshot_survives_client_close_during_write(trace_dir):
    """A popped completed snapshot must still be written if the client closes immediately."""

    class SlowFirstWrite:
        def __init__(self, inner: TraceWriter):
            self.inner = inner
            self.first_write_started = asyncio.Event()
            self.release_first_write = asyncio.Event()
            self.write_count = 0

        async def write(self, record):
            self.write_count += 1
            if self.write_count == 1:
                self.first_write_started.set()
                await self.release_first_write.wait()
            await self.inner.write(record)

        def close(self):
            self.inner.close()

    allow_upstream_close = asyncio.Event()

    async def ws_upstream_handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            data = json.loads(msg.data)
            response_id = data["response_id"]
            await ws.send_json(
                {
                    "type": "response.completed",
                    "response": {
                        "id": response_id,
                        "model": data.get("model", "test-model"),
                        "status": "completed",
                        "output": [{"type": "message", "content": [{"type": "output_text", "text": response_id}]}],
                        "usage": {"input_tokens": 3, "output_tokens": 1},
                    },
                }
            )
            await allow_upstream_close.wait()
            await ws.close()
            break
        return ws

    store, session_id, inner_writer = _make_writer()
    writer = SlowFirstWrite(inner_writer)

    upstream_runner, upstream_port = await _start_ws_upstream(ws_upstream_handler)
    proxy_runner, proxy_port, proxy_session = await _start_proxy(
        f"http://127.0.0.1:{upstream_port}",
        writer,
        strip_prefix="/v1",
    )

    try:
        async with aiohttp.ClientSession() as client:
            ws = await client.ws_connect(f"http://127.0.0.1:{proxy_port}/v1/responses")
            await ws.send_json({"model": "gpt-test", "response_id": "resp1", "input": "first"})

            msg = await asyncio.wait_for(ws.receive(), timeout=5)
            assert msg.type == aiohttp.WSMsgType.TEXT
            assert json.loads(msg.data)["response"]["id"] == "resp1"
            await asyncio.wait_for(writer.first_write_started.wait(), timeout=5)

            close_task = asyncio.create_task(ws.close())
            await asyncio.sleep(0.05)
            assert _load_records(store, session_id) == []

            writer.release_first_write.set()
            await asyncio.wait_for(close_task, timeout=5)

        await asyncio.sleep(0.1)
        writer.close()

        records = _load_records(store, session_id)
        assert len(records) == 1
        assert records[0]["request"]["body"]["response_id"] == "resp1"
        assert records[0]["response"]["body"]["id"] == "resp1"

    finally:
        writer.release_first_write.set()
        allow_upstream_close.set()
        await proxy_session.close()
        await proxy_runner.cleanup()
        await upstream_runner.cleanup()


@pytest.mark.asyncio
async def test_websocket_duplicate_completed_keeps_pending_trailing_events(trace_dir):
    """Duplicate terminal events should not discard pending non-terminal events."""

    async def ws_upstream_handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                await ws.send_json(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp_same",
                            "status": "completed",
                            "output": [],
                        },
                    }
                )
                await ws.send_json(
                    {
                        "type": "response.output_item.done",
                        "output_index": 0,
                        "item": {
                            "type": "message",
                            "role": "assistant",
                            "status": "completed",
                            "content": [{"type": "output_text", "text": "LATE_TEXT"}],
                        },
                    }
                )
                await ws.send_json(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp_same",
                            "status": "completed",
                            "output": [],
                        },
                    }
                )
                await ws.close()
                break
        return ws

    store, session_id, writer = _make_writer()

    upstream_runner, upstream_port = await _start_ws_upstream(ws_upstream_handler)
    proxy_runner, proxy_port, proxy_session = await _start_proxy(
        f"http://127.0.0.1:{upstream_port}",
        writer,
        strip_prefix="/v1",
    )

    try:
        async with aiohttp.ClientSession() as client:
            ws = await client.ws_connect(f"http://127.0.0.1:{proxy_port}/v1/responses")
            await ws.send_json({"model": "gpt-test", "input": "hello"})

            received = []
            while True:
                msg = await asyncio.wait_for(ws.receive(), timeout=5)
                if msg.type == aiohttp.WSMsgType.TEXT:
                    received.append(json.loads(msg.data))
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.CLOSED,
                ):
                    break
            await ws.close()

        assert [event["type"] for event in received] == [
            "response.completed",
            "response.output_item.done",
            "response.completed",
        ]

        await asyncio.sleep(0.1)
        writer.close()

        records = _load_records(store, session_id)
        assert len(records) == 2
        assert [event["type"] for event in records[0]["response"]["ws_events"]] == ["response.completed"]
        assert [event["type"] for event in records[1]["response"]["ws_events"]] == [
            "response.output_item.done",
        ]
        assert records[1]["response"]["body"]["output"][0]["content"][0]["text"] == "LATE_TEXT"

    finally:
        await proxy_session.close()
        await proxy_runner.cleanup()
        await upstream_runner.cleanup()


@pytest.mark.asyncio
async def test_websocket_duplicate_completed_trailing_events_do_not_move_to_next_response(trace_dir):
    """Trailing events after a duplicate terminal event must not be attributed to the next response."""

    async def ws_upstream_handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        received = 0
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            received += 1
            if received == 1:
                await ws.send_json(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp_same",
                            "status": "completed",
                            "output": [],
                        },
                    }
                )
                await ws.send_json(
                    {
                        "type": "response.output_item.done",
                        "output_index": 0,
                        "item": {
                            "type": "message",
                            "role": "assistant",
                            "status": "completed",
                            "content": [{"type": "output_text", "text": "LATE_TEXT"}],
                        },
                    }
                )
                await ws.send_json(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp_same",
                            "status": "completed",
                            "output": [],
                        },
                    }
                )
                continue
            await ws.send_json({"type": "response.output_text.delta", "delta": "SECOND"})
            await ws.send_json(
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_two",
                        "status": "completed",
                        "output": [],
                    },
                }
            )
            await ws.close()
            break
        return ws

    store, session_id, writer = _make_writer()

    upstream_runner, upstream_port = await _start_ws_upstream(ws_upstream_handler)
    proxy_runner, proxy_port, proxy_session = await _start_proxy(
        f"http://127.0.0.1:{upstream_port}",
        writer,
        strip_prefix="/v1",
    )

    try:
        async with aiohttp.ClientSession() as client:
            ws = await client.ws_connect(f"http://127.0.0.1:{proxy_port}/v1/responses")
            await ws.send_json({"response_id": "req1", "model": "gpt-test", "input": "first"})
            for _ in range(3):
                msg = await asyncio.wait_for(ws.receive(), timeout=5)
                assert msg.type == aiohttp.WSMsgType.TEXT

            await asyncio.sleep(0.1)
            partial_records = _load_records(store, session_id)
            assert len(partial_records) == 2
            assert [event["type"] for event in partial_records[1]["response"]["ws_events"]] == [
                "response.output_item.done"
            ]
            assert partial_records[1]["response"]["body"]["output"][0]["content"][0]["text"] == "LATE_TEXT"

            await ws.send_json({"response_id": "req2", "model": "gpt-test", "input": "second"})
            while True:
                msg = await asyncio.wait_for(ws.receive(), timeout=5)
                if msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.CLOSED,
                ):
                    break
                assert msg.type == aiohttp.WSMsgType.TEXT
            await ws.close()

        await asyncio.sleep(0.1)
        writer.close()

        records = _load_records(store, session_id)
        assert len(records) == 3
        assert records[0]["request"]["body"]["response_id"] == "req1"
        assert [event["type"] for event in records[0]["response"]["ws_events"]] == ["response.completed"]
        assert records[1]["request"]["body"] is None
        assert [event["type"] for event in records[1]["response"]["ws_events"]] == ["response.output_item.done"]
        assert records[1]["response"]["body"]["output"][0]["content"][0]["text"] == "LATE_TEXT"
        assert records[2]["request"]["body"]["response_id"] == "req2"
        assert [event["type"] for event in records[2]["response"]["ws_events"]] == [
            "response.output_text.delta",
            "response.completed",
        ]
        assert "LATE_TEXT" not in json.dumps(records[2]["response"]["body"], ensure_ascii=False)

    finally:
        await proxy_session.close()
        await proxy_runner.cleanup()
        await upstream_runner.cleanup()


@pytest.mark.asyncio
async def test_websocket_duplicate_completed_does_not_flush_next_request(trace_dir):
    """Duplicate terminal events must not split a buffered next request from its response."""

    async def ws_upstream_handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        received = 0
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            received += 1
            if received == 1:
                await ws.send_json(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp_same",
                            "status": "completed",
                            "output": [],
                        },
                    }
                )
                continue
            await ws.send_json(
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_same",
                        "status": "completed",
                        "output": [],
                    },
                }
            )
            await ws.send_json(
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_two",
                        "status": "completed",
                        "output": [],
                    },
                }
            )
            await ws.close()
            break
        return ws

    store, session_id, writer = _make_writer()

    upstream_runner, upstream_port = await _start_ws_upstream(ws_upstream_handler)
    proxy_runner, proxy_port, proxy_session = await _start_proxy(
        f"http://127.0.0.1:{upstream_port}",
        writer,
        strip_prefix="/v1",
    )

    try:
        async with aiohttp.ClientSession() as client:
            ws = await client.ws_connect(f"http://127.0.0.1:{proxy_port}/v1/responses")
            await ws.send_json({"response_id": "req1", "model": "gpt-test", "input": "first"})

            msg = await asyncio.wait_for(ws.receive(), timeout=5)
            assert msg.type == aiohttp.WSMsgType.TEXT
            assert json.loads(msg.data)["response"]["id"] == "resp_same"

            await ws.send_json({"response_id": "req2", "model": "gpt-test", "input": "second"})
            received = []
            while True:
                msg = await asyncio.wait_for(ws.receive(), timeout=5)
                if msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.CLOSED,
                ):
                    break
                assert msg.type == aiohttp.WSMsgType.TEXT
                received.append(json.loads(msg.data)["response"]["id"])
            await ws.close()

        assert received == ["resp_same", "resp_two"]

        await asyncio.sleep(0.1)
        writer.close()

        records = _load_records(store, session_id)
        assert len(records) == 2
        assert records[0]["request"]["body"]["response_id"] == "req1"
        assert records[0]["response"]["body"]["id"] == "resp_same"
        assert records[1]["request"]["body"]["response_id"] == "req2"
        assert records[1]["response"]["body"]["id"] == "resp_two"

    finally:
        await proxy_session.close()
        await proxy_runner.cleanup()
        await upstream_runner.cleanup()


@pytest.mark.asyncio
async def test_websocket_completed_segments_do_not_accumulate_in_memory(trace_dir):
    """Completed WS response segments should be released while the socket stays open."""
    allow_close = asyncio.Event()
    response_count = 200
    payload = "x" * 8_000

    async def ws_upstream_handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        received = 0
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)
                response_id = data["response_id"]
                await ws.send_json({"type": "response.output_text.delta", "delta": payload})
                await ws.send_json(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": response_id,
                            "status": "completed",
                            "output": [],
                        },
                    }
                )
                received += 1
                if received == response_count:
                    await allow_close.wait()
                    await ws.close()
                    break
        return ws

    store, session_id, writer = _make_writer()

    upstream_runner, upstream_port = await _start_ws_upstream(ws_upstream_handler)
    proxy_runner, proxy_port, proxy_session = await _start_proxy(
        f"http://127.0.0.1:{upstream_port}",
        writer,
        strip_prefix="/v1",
    )

    try:
        tracemalloc.start()
        async with aiohttp.ClientSession() as client:
            ws = await client.ws_connect(f"http://127.0.0.1:{proxy_port}/v1/responses")
            for index in range(response_count):
                await ws.send_json(
                    {
                        "type": "response.create",
                        "response_id": f"resp_{index}",
                        "model": "gpt-test",
                        "input": payload,
                    }
                )
                for _ in range(2):
                    msg = await asyncio.wait_for(ws.receive(), timeout=5)
                    assert msg.type == aiohttp.WSMsgType.TEXT

            await asyncio.sleep(0.1)
            current_bytes, peak_bytes = tracemalloc.get_traced_memory()
            record_count = len(_load_records(store, session_id))
            assert record_count == response_count

            allow_close.set()
            await ws.close()
        tracemalloc.stop()

        writer.close()
        assert current_bytes < 2_000_000
        assert peak_bytes < 8_000_000

    finally:
        allow_close.set()
        if tracemalloc.is_tracing():
            tracemalloc.stop()
        await proxy_session.close()
        await proxy_runner.cleanup()
        await upstream_runner.cleanup()


# ---------------------------------------------------------------------------
# Test 2: upstream ws_connect should inherit trust_env proxy behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_websocket_upstream_connect_does_not_override_proxy(trace_dir):
    """Upstream ws_connect must not force proxy=None; rely on session trust_env."""

    async def ws_upstream_handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                await ws.send_str(msg.data)
                await ws.close()
                break
        return ws

    store, session_id, writer = _make_writer()

    upstream_runner, upstream_port = await _start_ws_upstream(ws_upstream_handler)
    proxy_runner, proxy_port, proxy_session = await _start_proxy(
        f"http://127.0.0.1:{upstream_port}",
        writer,
        strip_prefix="/v1",
    )

    ws_connect_calls: list[dict] = []
    original_ws_connect = proxy_session.ws_connect

    async def _spy_ws_connect(*args, **kwargs):
        ws_connect_calls.append(dict(kwargs))
        return await original_ws_connect(*args, **kwargs)

    proxy_session.ws_connect = _spy_ws_connect  # type: ignore[method-assign]

    try:
        async with aiohttp.ClientSession() as client:
            ws = await client.ws_connect(f"http://127.0.0.1:{proxy_port}/v1/responses")
            await ws.send_str("hello")
            msg = await asyncio.wait_for(ws.receive(), timeout=5)
            assert msg.type == aiohttp.WSMsgType.TEXT
            assert msg.data == "hello"
            await ws.close()

        assert ws_connect_calls
        assert "proxy" not in ws_connect_calls[0]
    finally:
        await proxy_session.close()
        await proxy_runner.cleanup()
        await upstream_runner.cleanup()


# ---------------------------------------------------------------------------
# Test 3: upstream WebSocket failure returns 502
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_websocket_upstream_failure(trace_dir):
    """When upstream WS connect fails, return HTTP 502 and record the error."""

    store, session_id, writer = _make_writer()

    # Point proxy at a port where nothing is listening
    proxy_runner, proxy_port, proxy_session = await _start_proxy(
        "http://127.0.0.1:19999",
        writer,
        strip_prefix="/v1",
    )

    try:
        async with aiohttp.ClientSession() as client:
            # Attempt WebSocket upgrade — proxy should return 502
            with pytest.raises(aiohttp.WSServerHandshakeError) as exc_info:
                await client.ws_connect(f"http://127.0.0.1:{proxy_port}/v1/responses")
            assert exc_info.value.status == 502

        await asyncio.sleep(0.1)
        writer.close()

        records = store.load_records(session_id)
        assert len(records) == 1
        r = records[0]
        assert r["transport"] == "websocket"
        assert r["response"]["status"] == 502
        assert r["response"]["error"]

    finally:
        await proxy_session.close()
        await proxy_runner.cleanup()


def test_build_ws_record_merges_incremental_request_and_output_items() -> None:
    record = _build_ws_record(
        req_id="req_test",
        turn=1,
        duration_ms=25,
        path_qs="/v1/responses",
        req_headers={"Authorization": "Bearer test-token"},
        client_messages=[
            json.dumps(
                {
                    "type": "response.create",
                    "model": "gpt-5.4",
                    "instructions": "You are Codex.",
                    "input": [],
                    "tools": [{"type": "function", "name": "exec_command"}],
                }
            ),
            json.dumps(
                {
                    "type": "response.create",
                    "input": [
                        {"role": "user", "content": [{"type": "input_text", "text": "你好，调用一个工具，然后结束"}]}
                    ],
                }
            ),
            json.dumps(
                {
                    "type": "response.create",
                    "previous_response_id": "resp_previous",
                    "input": [
                        {
                            "type": "function_call_output",
                            "call_id": "call_1",
                            "output": "tool output",
                        }
                    ],
                }
            ),
        ],
        server_messages=[
            json.dumps(
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_1",
                        "status": "completed",
                        "output": [],
                        "usage": {"input_tokens": 10, "output_tokens": 0},
                    },
                }
            ),
            json.dumps(
                {
                    "type": "response.output_item.done",
                    "output_index": 1,
                    "item": {
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": "HELLO_FROM_WS"}],
                    },
                }
            ),
            json.dumps(
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_1",
                        "status": "completed",
                        "output": [],
                        "usage": {"input_tokens": 10, "output_tokens": 2},
                    },
                }
            ),
        ],
        upstream_base_url="https://chatgpt.com/backend-api/codex",
    )

    assert record["request"]["body"]["input"][0]["content"][0]["text"] == "你好，调用一个工具，然后结束"
    assert record["request"]["body"]["input"][1]["type"] == "function_call_output"
    assert record["request"]["body"]["previous_response_id"] == "resp_previous"
    assert record["request"]["body"]["tools"][0]["name"] == "exec_command"
    assert len(record["request"]["ws_events"]) == 3
    assert record["request"]["ws_events"][1]["input"][0]["content"][0]["text"] == "你好，调用一个工具，然后结束"
    assert record["request"]["ws_events"][2]["input"][0]["type"] == "function_call_output"
    assert record["response"]["body"]["usage"] == {"input_tokens": 10, "output_tokens": 2}
    assert record["response"]["body"]["output"][0]["content"][0]["text"] == "HELLO_FROM_WS"


def test_build_ws_record_treats_empty_error_string_as_failure() -> None:
    record = _build_ws_record(
        req_id="req_fail",
        turn=1,
        duration_ms=25,
        path_qs="/v1/responses",
        req_headers={},
        client_messages=[],
        server_messages=[],
        upstream_base_url="https://chatgpt.com/backend-api/codex",
        error="",
    )

    assert record["response"]["status"] == 502
    assert record["response"]["error"] == ""


# ---------------------------------------------------------------------------
# Test 4: WebSocket coexists with HTTP — mixed traffic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_websocket_and_http_coexist(trace_dir):
    """Both HTTP and WebSocket requests through the same proxy are recorded."""

    async def mixed_handler(request):
        if request.headers.get("Upgrade", "").lower() == "websocket":
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await ws.send_json({"type": "response.completed", "response": {"id": "ws_1"}})
                    await ws.close()
                    break
            return ws
        else:
            body = await request.json()
            return web.json_response(
                {
                    "id": "http_1",
                    "content": [{"type": "text", "text": "HTTP response"}],
                    "model": body.get("model", "test"),
                    "usage": {"input_tokens": 5, "output_tokens": 3},
                }
            )

    store, session_id, writer = _make_writer()

    upstream_runner, upstream_port = await _start_ws_upstream(mixed_handler)
    proxy_runner, proxy_port, proxy_session = await _start_proxy(
        f"http://127.0.0.1:{upstream_port}",
        writer,
    )

    try:
        async with aiohttp.ClientSession() as client:
            # HTTP request
            resp = await client.post(
                f"http://127.0.0.1:{proxy_port}/v1/messages",
                json={"model": "test-model", "messages": [{"role": "user", "content": "hi"}]},
            )
            assert resp.status == 200
            await resp.json()

            # WebSocket request
            ws = await client.ws_connect(f"http://127.0.0.1:{proxy_port}/v1/responses")
            await ws.send_json({"model": "test-model", "input": "ws hello"})
            msg = await asyncio.wait_for(ws.receive(), timeout=5)
            assert msg.type == aiohttp.WSMsgType.TEXT
            # Wait for close
            await asyncio.wait_for(ws.receive(), timeout=5)
            await ws.close()

        await asyncio.sleep(0.1)
        writer.close()

        records = store.load_records(session_id)
        assert len(records) == 2

        http_rec = next(r for r in records if r.get("transport") != "websocket")
        ws_rec = next(r for r in records if r.get("transport") == "websocket")

        assert http_rec["request"]["method"] == "POST"
        assert http_rec["response"]["status"] == 200

        assert ws_rec["request"]["method"] == "WEBSOCKET"
        assert ws_rec["response"]["status"] == 101

    finally:
        await proxy_session.close()
        await proxy_runner.cleanup()
        await upstream_runner.cleanup()


# ---------------------------------------------------------------------------
# Test 4: _get_ws_proxy_settings resolves proxy/auth from env
# ---------------------------------------------------------------------------


class TestGetWsProxySettings:
    """Unit tests for _get_ws_proxy_settings helper."""

    @pytest.fixture(autouse=True)
    def _clean_proxy_env(self, monkeypatch):
        """Remove all proxy env vars so tests control them explicitly."""
        for var in (
            "HTTPS_PROXY",
            "https_proxy",
            "HTTP_PROXY",
            "http_proxy",
            "ALL_PROXY",
            "all_proxy",
            "NO_PROXY",
            "no_proxy",
        ):
            monkeypatch.delenv(var, raising=False)

    def test_wss_uses_https_proxy(self, monkeypatch):
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy:8080")
        result = _get_ws_proxy_settings("wss://api.openai.com/v1/responses")
        assert result == (URL("http://proxy:8080"), None)

    def test_ws_uses_http_proxy(self, monkeypatch):
        monkeypatch.setenv("HTTP_PROXY", "http://proxy:3128")
        result = _get_ws_proxy_settings("ws://localhost/v1/responses")
        assert result == (URL("http://proxy:3128"), None)

    def test_proxy_auth_is_preserved(self, monkeypatch):
        monkeypatch.setenv("HTTPS_PROXY", "http://user:pass@proxy:8080")
        result = _get_ws_proxy_settings("wss://api.openai.com/v1/responses")
        assert result is not None
        proxy_url, proxy_auth = result
        assert proxy_url == URL("http://proxy:8080")
        assert proxy_auth is not None
        assert proxy_auth.login == "user"
        assert proxy_auth.password == "pass"

    def test_no_proxy_returns_none(self, monkeypatch):
        # Mock get_env_proxy_for_url to raise LookupError (no proxy configured).
        # Necessary because macOS system proxy settings bypass env vars.
        monkeypatch.setattr(
            "claude_tap.ws_proxy.get_env_proxy_for_url",
            lambda url: (_ for _ in ()).throw(LookupError("no proxy")),
        )
        result = _get_ws_proxy_settings("wss://api.openai.com/v1/responses")
        assert result is None

    def test_non_ws_scheme_returns_none(self):
        result = _get_ws_proxy_settings("https://api.openai.com/v1/responses")
        assert result is None
