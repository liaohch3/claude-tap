"""Focused tests for forward-proxy request capture."""

from __future__ import annotations

import gzip
import json
import zlib
from pathlib import Path
from typing import Any, Callable

import pytest

from claude_tap.cli_clients import CLIENT_CONFIGS
from claude_tap.forward_proxy import ForwardProxyServer, _decode_request_body_for_trace
from claude_tap.trace import TraceWriter
from claude_tap.trace_store import get_trace_store, reset_trace_store

try:
    from compression import zstd
except ImportError:
    import backports.zstd as zstd


class _UnexpectedSession:
    async def request(self, **_kwargs: Any) -> None:
        raise AssertionError("capture-only mode must not contact upstream")


class _MemoryWriter:
    def __init__(self) -> None:
        self.data = bytearray()

    def write(self, data: bytes) -> None:
        self.data.extend(data)

    async def drain(self) -> None:
        return None


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("POST", "/backend-api/codex/responses", True),
        ("POST", "/v1/responses", True),
        ("WEBSOCKET", "/v1/responses", True),
        ("GET", "/v1/responses", False),
        ("POST", "/v1/chat/completions", False),
        ("POST", "/v1/responses-other", False),
    ],
)
def test_codexapp_capture_filter(method: str, path: str, expected: bool) -> None:
    cfg = CLIENT_CONFIGS["codexapp"]
    server = ForwardProxyServer(
        host="127.0.0.1",
        port=0,
        ca=object(),
        writer=object(),
        session=object(),
        trace_methods=cfg.forward_trace_methods,
        trace_path_prefixes=cfg.forward_trace_path_prefixes,
    )

    assert server._should_trace_request(method, path) is expected


@pytest.mark.asyncio
async def test_forward_proxy_captures_codexapp_custom_responses_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLOUDTAP_DB", str(tmp_path / "traces.sqlite3"))
    reset_trace_store()
    store = get_trace_store()
    session_id = store.create_session(client="codexapp", proxy_mode="forward")
    trace_writer = TraceWriter(session_id, store=store)
    client_writer = _MemoryWriter()
    cfg = CLIENT_CONFIGS["codexapp"]
    server = ForwardProxyServer(
        host="127.0.0.1",
        port=0,
        ca=object(),
        writer=trace_writer,
        session=_UnexpectedSession(),
        trace_methods=cfg.forward_trace_methods,
        trace_path_prefixes=cfg.forward_trace_path_prefixes,
        capture_only=True,
    )
    request_body = {
        "model": "MiniMax-M3",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
        "stream": False,
    }

    try:
        await server._forward_and_record(
            "POST",
            "/v1/responses",
            {"Content-Type": "application/json"},
            json.dumps(request_body).encode(),
            "https://api.minimaxi.com/v1/responses",
            client_writer,
        )
        trace_writer.close()

        records = store.load_records(session_id)
        assert len(records) == 1
        assert records[0]["request"]["path"] == "/v1/responses"
        assert records[0]["request"]["body"] == request_body
        assert records[0]["response"]["status"] == 200
    finally:
        reset_trace_store()


@pytest.mark.parametrize(
    ("encoding", "compress"),
    [
        ("gzip", gzip.compress),
        ("deflate", zlib.compress),
        ("zstd", zstd.compress),
    ],
)
def test_decode_request_body_for_trace_supports_common_encodings(
    encoding: str,
    compress: Callable[[bytes], bytes],
) -> None:
    payload = b'{"model":"gpt-5.6-luna","stream":true}'
    assert _decode_request_body_for_trace(compress(payload), {"Content-Encoding": encoding}) == payload


def test_decode_request_body_for_trace_leaves_unknown_or_broken_bodies() -> None:
    payload = b'{"model":"gpt-5.6-luna"}'
    assert _decode_request_body_for_trace(payload, {}) == payload
    assert _decode_request_body_for_trace(payload, {"Content-Encoding": "identity"}) == payload
    assert _decode_request_body_for_trace(payload, {"Content-Encoding": "br"}) == payload
    assert _decode_request_body_for_trace(b"not-zstd", {"Content-Encoding": "zstd"}) == b"not-zstd"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("encoding", "compress"),
    [
        ("gzip", gzip.compress),
        ("deflate", zlib.compress),
        ("zstd", zstd.compress),
    ],
)
async def test_forward_proxy_captures_compressed_pi_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    encoding: str,
    compress: Callable[[bytes], bytes],
) -> None:
    monkeypatch.setenv("CLOUDTAP_DB", str(tmp_path / "traces.sqlite3"))
    reset_trace_store()
    store = get_trace_store()
    session_id = store.create_session(client="pi", proxy_mode="forward")
    trace_writer = TraceWriter(session_id, store=store)
    client_writer = _MemoryWriter()
    server = ForwardProxyServer(
        host="127.0.0.1",
        port=0,
        ca=object(),
        writer=trace_writer,
        session=_UnexpectedSession(),
        store_stream_events=True,
        capture_only=True,
    )
    request_body = {
        "model": "gpt-5.6-luna",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        "stream": True,
    }
    compressed_body = compress(json.dumps(request_body).encode())

    try:
        await server._forward_and_record(
            "POST",
            "/backend-api/codex/responses",
            {"Content-Type": "application/json", "Content-Encoding": encoding},
            compressed_body,
            "https://chatgpt.com/backend-api/codex/responses",
            client_writer,
        )
        trace_writer.close()

        records = store.load_records(session_id)
        assert len(records) == 1
        assert records[0]["request"]["body"] == request_body
        assert records[0]["response"]["body"]["output"][0]["content"][0]["text"] == "captured"
        assert b"Content-Type: text/event-stream" in client_writer.data
    finally:
        reset_trace_store()
