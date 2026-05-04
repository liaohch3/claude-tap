"""Regression: SSEReassembler must capture OpenAI Chat Completions streams.

opencode in forward-proxy mode talks to providers that expose the OpenAI
Chat Completions API (e.g. opencode.ai's `/zen/v1/chat/completions`). Those
streams use bare `data: {...}` frames with no `event:` headers, terminated
by `data: [DONE]`. The pre-fix reassembler required an explicit `event:`
line and silently dropped every Chat Completions response — opencode traces
showed `resp.body=None` and `sse_events=[]` for any non-Anthropic provider.
"""

from __future__ import annotations

from claude_tap.sse import SSEReassembler


def test_chat_completions_stream_events_are_captured() -> None:
    r = SSEReassembler()
    r.feed_bytes(
        b'data: {"id":"c1","choices":[{"delta":{"role":"assistant"}}]}\n\n'
        b'data: {"id":"c1","choices":[{"delta":{"content":"OK"}}]}\n\n'
        b'data: {"id":"c1","choices":[{"finish_reason":"stop","delta":{}}]}\n\n'
        b"data: [DONE]\n\n"
    )

    # All three real frames captured; [DONE] is filtered as protocol noise.
    assert len(r.events) == 3
    for ev in r.events:
        assert ev["event"] == "message"
    assert r.events[0]["data"]["choices"][0]["delta"] == {"role": "assistant"}
    assert r.events[1]["data"]["choices"][0]["delta"] == {"content": "OK"}
    assert r.events[2]["data"]["choices"][0]["finish_reason"] == "stop"

    # Snapshot is reconstructed so the viewer's Response section can render.
    snap = r.reconstruct()
    assert snap is not None
    # True OpenAI Chat Completions shape preserved for fidelity.
    assert snap["choices"][0]["message"]["role"] == "assistant"
    assert snap["choices"][0]["message"]["content"] == "OK"
    assert snap["choices"][0]["finish_reason"] == "stop"
    # Anthropic-shape mirror so the existing renderer picks it up unchanged.
    assert snap["content"] == [{"type": "text", "text": "OK"}]


def test_chat_completions_usage_dual_naming() -> None:
    """Final chunk's `usage` must be exposed under both Anthropic and OpenAI
    keys so token displays that only know one schema still work."""
    r = SSEReassembler()
    r.feed_bytes(
        b'data: {"id":"c1","model":"hy3","choices":[{"delta":{"role":"assistant","content":"hi"}}]}\n\n'
        b'data: {"id":"c1","choices":[{"delta":{},"finish_reason":"stop"}],'
        b'"usage":{"prompt_tokens":12,"completion_tokens":3,"total_tokens":15}}\n\n'
        b"data: [DONE]\n\n"
    )
    snap = r.reconstruct()
    assert snap is not None
    usage = snap["usage"]
    # OpenAI naming preserved
    assert usage["prompt_tokens"] == 12
    assert usage["completion_tokens"] == 3
    # Anthropic-aliased copies added so existing stat extractors pick them up
    assert usage["input_tokens"] == 12
    assert usage["output_tokens"] == 3
    assert snap["model"] == "hy3"


def test_chat_completions_tool_call_accumulation() -> None:
    """Tool calls stream as indexed deltas with name/arguments concatenated
    across multiple chunks. Final snapshot must have the assembled call."""
    r = SSEReassembler()
    r.feed_bytes(
        b'data: {"id":"c1","choices":[{"delta":{"role":"assistant","tool_calls":'
        b'[{"index":0,"id":"call_1","type":"function","function":{"name":"get_weather","arguments":""}}]}}]}\n\n'
        b'data: {"id":"c1","choices":[{"delta":{"tool_calls":'
        b'[{"index":0,"function":{"arguments":"{\\"city\\":"}}]}}]}\n\n'
        b'data: {"id":"c1","choices":[{"delta":{"tool_calls":'
        b'[{"index":0,"function":{"arguments":"\\"SF\\"}"}}]}}]}\n\n'
        b'data: {"id":"c1","choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n'
        b"data: [DONE]\n\n"
    )
    snap = r.reconstruct()
    assert snap is not None
    msg = snap["choices"][0]["message"]
    assert msg["tool_calls"][0]["id"] == "call_1"
    assert msg["tool_calls"][0]["function"]["name"] == "get_weather"
    assert msg["tool_calls"][0]["function"]["arguments"] == '{"city":"SF"}'
    assert snap["choices"][0]["finish_reason"] == "tool_calls"


def test_chat_completions_done_sentinel_is_filtered() -> None:
    r = SSEReassembler()
    r.feed_bytes(b"data: [DONE]\n\n")
    assert r.events == []


def test_chat_completions_chunked_across_feeds() -> None:
    """Bytes split mid-frame must still produce one coherent event."""
    r = SSEReassembler()
    r.feed_bytes(b'data: {"id":"c1","choices":[{"de')
    r.feed_bytes(b'lta":{"content":"hel')
    r.feed_bytes(b'lo"}}]}\n\n')
    assert len(r.events) == 1
    assert r.events[0]["data"]["choices"][0]["delta"]["content"] == "hello"


def test_anthropic_stream_unchanged() -> None:
    """The Chat Completions support must not regress Anthropic snapshot
    reconstruction — it's the path claude/codex rely on."""
    r = SSEReassembler()
    r.feed_bytes(
        b"event: message_start\n"
        b'data: {"type":"message_start","message":{"id":"m1","role":"assistant",'
        b'"content":[],"model":"claude-x","usage":{"input_tokens":3,"output_tokens":0}}}\n\n'
        b"event: content_block_start\n"
        b'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n'
        b"event: content_block_delta\n"
        b'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"hi"}}\n\n'
        b"event: content_block_stop\n"
        b'data: {"type":"content_block_stop","index":0}\n\n'
        b"event: message_delta\n"
        b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":1}}\n\n'
        b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
    )
    snap = r.reconstruct()
    assert snap is not None
    assert snap["content"][0]["text"] == "hi"
    assert snap["usage"]["output_tokens"] == 1


def test_mixed_event_and_bare_data_in_one_stream() -> None:
    """Defensive: a stream that mixes both shapes shouldn't crash. The bare
    frames emit as default-type events; the named ones use their declared name."""
    r = SSEReassembler()
    r.feed_bytes(b'data: {"bare":1}\n\nevent: ping\ndata: {"named":2}\n\ndata: {"bare":3}\n\n')
    assert [e["event"] for e in r.events] == ["message", "ping", "message"]
    assert [e["data"] for e in r.events] == [{"bare": 1}, {"named": 2}, {"bare": 3}]
