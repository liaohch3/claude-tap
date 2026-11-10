from __future__ import annotations

import asyncio
import json
import logging
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer, make_mocked_request

from claude_tap.certs import CertificateAuthority
from claude_tap.forward_proxy import ForwardProxyServer
from claude_tap.proxy import SENSITIVE_HEADER_KEYS, _handle_streaming, filter_headers, proxy_handler
from claude_tap.trace import TraceWriter
from claude_tap.trace_log_handler import SQLiteLogHandler
from claude_tap.trace_store import TraceStore
from claude_tap.viewer import _generate_html_viewer_from_records


@pytest.mark.parametrize("header", sorted(SENSITIVE_HEADER_KEYS))
@pytest.mark.parametrize("value", ["", "short", "exactly12chr", "INERT_HEADER_SENTINEL_WITH_NO_CREDENTIAL"])
def test_sensitive_headers_are_fully_masked_without_mutating_forwarded_values(header, value):
    headers = {header.upper(): value, "Content-Type": "application/json"}

    assert filter_headers(headers, redact_keys=True) == {header.upper(): "***", "Content-Type": "application/json"}
    assert filter_headers(headers) == headers


@pytest.mark.parametrize("mode", ["reverse", "forward"])
@pytest.mark.parametrize("response_kind", ["json", "sse", "error"])
async def test_auth_headers_reach_only_fake_upstream(tmp_path, mode, response_kind):
    headers = {
        "aUtHoRiZaTiOn": "Bearer AUTH_SENTINEL_NO_CREDENTIAL_123456789",
        "X-aPi-KeY": "API_SENTINEL_NO_CREDENTIAL_123456789",
    }
    received_headers = []

    async def upstream_handler(request):
        received_headers.append({name: request.headers[name] for name in headers})
        assert (await request.json())["messages"][0]["content"] == "inert prompt " * 300
        if response_kind == "sse":
            response = web.StreamResponse(headers={**headers, "Content-Type": "text/event-stream"})
            await response.prepare(request)
            await response.write(
                b'event: message_start\ndata: {"type":"message_start","message":{"id":"test","content":[]}}\n\n'
                b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
            )
            await response.write_eof()
            return response
        return web.json_response(
            {"result": "inert response"}, status=401 if response_kind == "error" else 200, headers=headers
        )

    store = TraceStore(tmp_path / "sentinel.sqlite3")
    session_id = store.create_session()
    writer = TraceWriter(session_id, store=store)
    logger = logging.getLogger("claude-tap")
    handler = SQLiteLogHandler(session_id, store=store)
    previous_level = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    upstream_app = web.Application()
    upstream_app.router.add_post("/v1/messages", upstream_handler)
    try:
        async with TestServer(upstream_app) as upstream, aiohttp.ClientSession(trust_env=False) as session:
            proxy = None
            reverse_server = None
            if mode == "reverse":
                proxy_app = web.Application()
                proxy_app["trace_ctx"] = {
                    "target_url": str(upstream.make_url("/")).rstrip("/"),
                    "writer": writer,
                    "session": session,
                    "store_stream_events": True,
                }
                proxy_app.router.add_post("/v1/messages", proxy_handler)
                reverse_server = TestServer(proxy_app)
                await reverse_server.start_server()
                url = reverse_server.make_url("/v1/messages")
                proxy_url = None
            else:
                proxy = ForwardProxyServer(
                    "127.0.0.1", 0, Mock(spec=CertificateAuthority), writer, session, store_stream_events=True
                )
                proxy_port = await proxy.start()
                url = upstream.make_url("/v1/messages")
                proxy_url = f"http://127.0.0.1:{proxy_port}"
            try:
                async with session.post(
                    url,
                    proxy=proxy_url,
                    headers=headers,
                    json={
                        "model": "inert-model",
                        "messages": [{"role": "user", "content": "inert prompt " * 300}],
                        "stream": response_kind == "sse",
                    },
                ) as response:
                    assert response.status == (401 if response_kind == "error" else 200)
                    assert all(response.headers[name] == value for name, value in headers.items())
                    await response.read()
            finally:
                if reverse_server is not None:
                    await reverse_server.close()
                if proxy is not None:
                    await proxy.stop()

        assert received_headers == [headers]
        writer.close()
        records = store.load_records(session_id)
        assert len(records) == 1
        for direction in ("request", "response"):
            recorded_headers = {name.lower(): value for name, value in records[0][direction]["headers"].items()}
            assert recorded_headers["authorization"] == "***"
            assert recorded_headers["x-api-key"] == "***"
        assert store.export_log(session_id)
        assert store._connect().execute("SELECT COUNT(*) FROM record_blobs").fetchone()[0] > 0
        (tmp_path / "export.jsonl").write_text(store.export_jsonl(session_id), encoding="utf-8")
        (tmp_path / "export.ctap").write_text(store.export_compact(session_id), encoding="utf-8")
        (tmp_path / "export.log").write_text(store.export_log(session_id), encoding="utf-8")
        _generate_html_viewer_from_records(
            [json.dumps(record) for record in records],
            tmp_path / "export.html",
            display_trace_path="export.jsonl",
            display_html_path="export.html",
        )
        assert (tmp_path / "sentinel.sqlite3-wal").exists()
        for path in tmp_path.iterdir():
            if path.is_file():
                contents = path.read_bytes()
                for value in headers.values():
                    assert value.encode() not in contents, path.name
                    assert value[:12].encode() not in contents, path.name
        assert all(value not in json.dumps(records) for value in headers.values())
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)
        handler.close()
        store.close()


@pytest.mark.parametrize("mode", ["reverse", "forward"])
async def test_cancelled_stream_persists_only_masked_headers(tmp_path, monkeypatch, mode):
    headers = {
        "Authorization": "Bearer CANCEL_SENTINEL_NO_CREDENTIAL",
        "x-api-key": "CANCEL_API_SENTINEL_NO_CREDENTIAL",
    }

    async def chunks():
        yield b'event: message_start\ndata: {"type":"message_start","message":{"id":"test","content":[]}}\n\n'
        raise asyncio.CancelledError

    upstream = SimpleNamespace(
        status=200,
        reason="OK",
        headers={**headers, "Content-Type": "text/event-stream"},
        content=SimpleNamespace(iter_any=chunks),
    )
    store = TraceStore(tmp_path / "cancel.sqlite3")
    session_id = store.create_session()
    writer = TraceWriter(session_id, store=store)
    try:
        async with aiohttp.ClientSession(trust_env=False) as session:
            if mode == "reverse":
                downstream = SimpleNamespace(prepare=AsyncMock(), write=AsyncMock(), write_eof=AsyncMock())
                monkeypatch.setattr("claude_tap.proxy.web.StreamResponse", lambda **kwargs: downstream)
                request = make_mocked_request("POST", "/v1/messages", headers=headers)
                await _handle_streaming(
                    request,
                    upstream,
                    "cancel",
                    1,
                    time.monotonic(),
                    {},
                    writer,
                    "[test]",
                    "http://127.0.0.1",
                    True,
                    True,
                )
            else:
                proxy = ForwardProxyServer("127.0.0.1", 0, Mock(spec=CertificateAuthority), writer, session)
                downstream = SimpleNamespace(write=Mock(), drain=AsyncMock())
                await proxy._handle_streaming(
                    upstream,
                    downstream,
                    "cancel",
                    1,
                    time.monotonic(),
                    "POST",
                    "/v1/messages",
                    headers,
                    {},
                    "[test]",
                    "http://127.0.0.1",
                )
        records = store.load_records(session_id)
        assert len(records) == 1
        for direction in ("request", "response"):
            assert records[0][direction]["headers"]["Authorization"] == "***"
            assert records[0][direction]["headers"]["x-api-key"] == "***"
        for path in tmp_path.iterdir():
            if path.is_file():
                contents = path.read_bytes()
                assert all(value[:12].encode() not in contents for value in headers.values())
    finally:
        store.close()
