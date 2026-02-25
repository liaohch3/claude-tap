"""Tests for SSEReassembler – SSE parsing and message reconstruction.

Focuses on the streaming protocol branches not covered by existing tests:
thinking_delta, content_block_stop with partial JSON, and edge cases.
"""

import json

from claude_tap.sse import SSEReassembler


def _feed_events(reassembler: SSEReassembler, events: list[tuple[str, dict]]):
    """Feed a sequence of (event_type, data) pairs as raw SSE bytes."""
    for event_type, data in events:
        raw = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
        reassembler.feed_bytes(raw.encode())


# ---------------------------------------------------------------------------
# Thinking delta handling
# ---------------------------------------------------------------------------


class TestThinkingDelta:
    """Test accumulation of thinking_delta events (extended thinking)."""

    def test_thinking_delta_accumulates(self):
        """thinking_delta events should accumulate into a thinking block."""
        r = SSEReassembler()
        _feed_events(
            r,
            [
                ("message_start", {"message": {"id": "msg_1", "content": [], "usage": {}}}),
                ("content_block_start", {"index": 0, "content_block": {"type": "thinking", "thinking": ""}}),
                ("content_block_delta", {"index": 0, "delta": {"type": "thinking_delta", "thinking": "Let me "}}),
                ("content_block_delta", {"index": 0, "delta": {"type": "thinking_delta", "thinking": "think..."}}),
                ("content_block_stop", {"index": 0}),
            ],
        )
        result = r.reconstruct()
        assert result["content"][0]["type"] == "thinking"
        assert result["content"][0]["thinking"] == "Let me think..."

    def test_thinking_mixed_with_text(self):
        """A response with both thinking and text blocks."""
        r = SSEReassembler()
        _feed_events(
            r,
            [
                ("message_start", {"message": {"id": "msg_1", "content": [], "usage": {}}}),
                # Thinking block
                ("content_block_start", {"index": 0, "content_block": {"type": "thinking", "thinking": ""}}),
                ("content_block_delta", {"index": 0, "delta": {"type": "thinking_delta", "thinking": "reasoning"}}),
                ("content_block_stop", {"index": 0}),
                # Text block
                ("content_block_start", {"index": 1, "content_block": {"type": "text", "text": ""}}),
                ("content_block_delta", {"index": 1, "delta": {"type": "text_delta", "text": "The answer is 42"}}),
                ("content_block_stop", {"index": 1}),
            ],
        )
        result = r.reconstruct()
        assert len(result["content"]) == 2
        assert result["content"][0]["thinking"] == "reasoning"
        assert result["content"][1]["text"] == "The answer is 42"


# ---------------------------------------------------------------------------
# Tool use with partial JSON (input_json_delta / content_block_stop)
# ---------------------------------------------------------------------------


class TestToolUsePartialJSON:
    """Test tool_use blocks accumulated via input_json_delta events."""

    def test_partial_json_assembled_on_stop(self):
        """input_json_delta chunks should be assembled into 'input' on content_block_stop."""
        r = SSEReassembler()
        _feed_events(
            r,
            [
                ("message_start", {"message": {"id": "msg_1", "content": [], "usage": {}}}),
                (
                    "content_block_start",
                    {"index": 0, "content_block": {"type": "tool_use", "id": "toolu_1", "name": "read_file"}},
                ),
                ("content_block_delta", {"index": 0, "delta": {"type": "input_json_delta", "partial_json": '{"file_'}}),
                ("content_block_delta", {"index": 0, "delta": {"type": "input_json_delta", "partial_json": 'path": '}}),
                (
                    "content_block_delta",
                    {"index": 0, "delta": {"type": "input_json_delta", "partial_json": '"test.py"}'}},
                ),
                ("content_block_stop", {"index": 0}),
            ],
        )
        result = r.reconstruct()
        block = result["content"][0]
        assert block["type"] == "tool_use"
        assert block["name"] == "read_file"
        assert block["input"] == {"file_path": "test.py"}
        assert "_partial_json" not in block  # Internal field should be cleaned up

    def test_invalid_partial_json_handled_gracefully(self):
        """If accumulated partial JSON is invalid, block should not have 'input' but also not crash."""
        r = SSEReassembler()
        _feed_events(
            r,
            [
                ("message_start", {"message": {"id": "msg_1", "content": [], "usage": {}}}),
                (
                    "content_block_start",
                    {"index": 0, "content_block": {"type": "tool_use", "id": "toolu_1", "name": "bash"}},
                ),
                (
                    "content_block_delta",
                    {"index": 0, "delta": {"type": "input_json_delta", "partial_json": '{"broken'}},
                ),
                ("content_block_stop", {"index": 0}),
            ],
        )
        result = r.reconstruct()
        block = result["content"][0]
        assert block["type"] == "tool_use"
        # input should not be set (JSON was invalid)
        assert "input" not in block
        # _partial_json should still be cleaned up
        assert "_partial_json" not in block


# ---------------------------------------------------------------------------
# message_delta with usage
# ---------------------------------------------------------------------------


class TestMessageDelta:
    """Test message_delta events (stop_reason, usage)."""

    def test_stop_reason_and_usage_applied(self):
        """message_delta should set stop_reason and merge usage."""
        r = SSEReassembler()
        _feed_events(
            r,
            [
                ("message_start", {"message": {"id": "msg_1", "content": [], "usage": {"input_tokens": 100}}}),
                ("content_block_start", {"index": 0, "content_block": {"type": "text", "text": ""}}),
                ("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": "hi"}}),
                ("content_block_stop", {"index": 0}),
                ("message_delta", {"delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 50}}),
            ],
        )
        result = r.reconstruct()
        assert result["stop_reason"] == "end_turn"
        assert result["usage"]["output_tokens"] == 50
        assert result["usage"]["input_tokens"] == 100


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestSSEEdgeCases:
    """Edge cases and error handling in SSE parsing."""

    def test_non_dict_data_ignored(self):
        """Events with non-dict data (e.g. '[DONE]') should be ignored."""
        r = SSEReassembler()
        # message_start to init snapshot
        _feed_events(
            r,
            [
                ("message_start", {"message": {"id": "msg_1", "content": [], "usage": {}}}),
            ],
        )
        # Feed a non-JSON event (like the OpenAI-style [DONE])
        r.feed_bytes(b"event: done\ndata: [DONE]\n\n")
        # Should not crash, snapshot should still be valid
        result = r.reconstruct()
        assert result["id"] == "msg_1"

    def test_event_before_message_start_ignored(self):
        """Events arriving before message_start should be ignored (no snapshot yet)."""
        r = SSEReassembler()
        _feed_events(
            r,
            [
                ("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": "orphan"}}),
            ],
        )
        assert r.reconstruct() is None

    def test_empty_bytes(self):
        """Feeding empty bytes should not crash."""
        r = SSEReassembler()
        r.feed_bytes(b"")
        assert r.reconstruct() is None

    def test_content_block_start_extends_content_list(self):
        """content_block_start with a high index should extend the content list."""
        r = SSEReassembler()
        _feed_events(
            r,
            [
                ("message_start", {"message": {"id": "msg_1", "content": [], "usage": {}}}),
                ("content_block_start", {"index": 2, "content_block": {"type": "text", "text": "hello"}}),
            ],
        )
        result = r.reconstruct()
        # Content list should be extended to index 2
        assert len(result["content"]) == 3
        assert result["content"][2]["type"] == "text"

    def test_reconstruct_returns_none_without_events(self):
        """Without any events, reconstruct() should return None."""
        r = SSEReassembler()
        assert r.reconstruct() is None

    def test_partial_sse_line_buffered(self):
        """Incomplete SSE lines should be buffered until newline arrives."""
        r = SSEReassembler()
        # Send event in fragments
        r.feed_bytes(b"event: message_start\n")
        r.feed_bytes(b'data: {"message": {"id": "msg_1", "cont')
        # No events parsed yet (no trailing \n\n)
        assert len(r.events) == 0
        r.feed_bytes(b'ent": [], "usage": {}}}\n\n')
        # Now it should be parsed
        assert len(r.events) == 1
        assert r.events[0]["event"] == "message_start"

    def test_events_list_captures_all_raw_events(self):
        """The events list should capture all SSE events for the trace record."""
        r = SSEReassembler()
        _feed_events(
            r,
            [
                ("message_start", {"message": {"id": "msg_1", "content": [], "usage": {}}}),
                ("content_block_start", {"index": 0, "content_block": {"type": "text", "text": ""}}),
                ("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": "hi"}}),
                ("content_block_stop", {"index": 0}),
                ("message_delta", {"delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 10}}),
            ],
        )
        assert len(r.events) == 5
        event_types = [e["event"] for e in r.events]
        assert event_types == [
            "message_start",
            "content_block_start",
            "content_block_delta",
            "content_block_stop",
            "message_delta",
        ]

    def test_delta_on_out_of_range_index_ignored(self):
        """A delta with index beyond content length should not crash."""
        r = SSEReassembler()
        _feed_events(
            r,
            [
                ("message_start", {"message": {"id": "msg_1", "content": [], "usage": {}}}),
                # Delta for index 5, but content list is empty
                ("content_block_delta", {"index": 5, "delta": {"type": "text_delta", "text": "orphan"}}),
            ],
        )
        result = r.reconstruct()
        # Should not crash; content is still empty since block_start never happened for index 5
        assert result["content"] == []
