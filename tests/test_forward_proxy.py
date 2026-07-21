"""Focused tests for forward-proxy request capture."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from claude_tap.forward_proxy import ForwardProxyServer
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


@pytest.mark.asyncio
async def test_forward_proxy_captures_zstd_compressed_pi_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    compressed_body = zstd.compress(json.dumps(request_body).encode())

    try:
        await server._forward_and_record(
            "POST",
            "/backend-api/codex/responses",
            {"Content-Type": "application/json", "Content-Encoding": "zstd"},
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
