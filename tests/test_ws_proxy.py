"""Tests for WebSocket proxy support in reverse proxy mode."""

import asyncio
import json
import shutil
import tempfile
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web
from yarl import URL

from claude_tap.proxy import _get_ws_proxy_settings, proxy_handler
from claude_tap.trace import TraceWriter


@pytest.fixture
def trace_dir():
    d = tempfile.mkdtemp(prefix="claude_tap_ws_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


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

    trace_path = Path(trace_dir) / "trace_ws.jsonl"
    writer = TraceWriter(trace_path)

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

        records = [json.loads(line) for line in trace_path.read_text().splitlines() if line.strip()]
        assert len(records) == 1
        r = records[0]
        assert r["transport"] == "websocket"
        assert r["request"]["method"] == "WEBSOCKET"
        assert r["request"]["path"] == "/v1/responses"
        assert r["request"]["body"]["model"] == "gpt-test"
        assert r["response"]["status"] == 101
        assert len(r["response"]["ws_events"]) == 4
        assert r["response"]["body"]["status"] == "completed"
        assert r["upstream_base_url"] == f"http://127.0.0.1:{upstream_port}"

    finally:
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

    trace_path = Path(trace_dir) / "trace_ws_proxy_args.jsonl"
    writer = TraceWriter(trace_path)

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

    trace_path = Path(trace_dir) / "trace_ws_fail.jsonl"
    writer = TraceWriter(trace_path)

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

        records = [json.loads(line) for line in trace_path.read_text().splitlines() if line.strip()]
        assert len(records) == 1
        r = records[0]
        assert r["transport"] == "websocket"
        assert r["response"]["status"] == 502
        assert r["response"]["error"]

    finally:
        await proxy_session.close()
        await proxy_runner.cleanup()


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

    trace_path = Path(trace_dir) / "trace_mixed.jsonl"
    writer = TraceWriter(trace_path)

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

        records = [json.loads(line) for line in trace_path.read_text().splitlines() if line.strip()]
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
            "claude_tap.proxy.get_env_proxy_for_url",
            lambda url: (_ for _ in ()).throw(LookupError("no proxy")),
        )
        result = _get_ws_proxy_settings("wss://api.openai.com/v1/responses")
        assert result is None

    def test_non_ws_scheme_returns_none(self):
        result = _get_ws_proxy_settings("https://api.openai.com/v1/responses")
        assert result is None
