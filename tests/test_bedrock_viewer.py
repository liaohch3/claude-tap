"""Tests for Bedrock EventStream trace normalization in the HTML viewer."""

from __future__ import annotations

import base64
import json

from claude_tap.viewer import _normalize_record_for_viewer


def _bedrock_frame(payload: dict) -> str:
    encoded = base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    return "\x00\x00binary-prefix" + json.dumps({"bytes": encoded, "p": "abcdefghijk"}) + "\ufffd"


def test_normalize_record_for_viewer_decodes_bedrock_eventstream() -> None:
    body = "".join(
        [
            _bedrock_frame(
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg_1",
                        "type": "message",
                        "role": "assistant",
                        "model": "claude-opus-4-6",
                        "content": [],
                        "usage": {
                            "input_tokens": 3,
                            "cache_creation_input_tokens": 0,
                            "cache_read_input_tokens": 0,
                            "output_tokens": 0,
                        },
                    },
                }
            ),
            _bedrock_frame({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
            _bedrock_frame({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "OK"}}),
            _bedrock_frame({"type": "content_block_stop", "index": 0}),
            _bedrock_frame(
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": 1},
                }
            ),
            _bedrock_frame({"type": "message_stop", "amazon-bedrock-invocationMetrics": {"inputTokenCount": 3}}),
        ]
    )
    record = {
        "turn": 1,
        "request": {
            "method": "POST",
            "path": "/model/global.anthropic.claude-opus-4-6-v1/invoke-with-response-stream",
            "body": {"messages": [{"role": "user", "content": [{"type": "text", "text": "ping"}]}]},
        },
        "response": {"status": 200, "headers": {}, "body": body},
    }

    normalized = json.loads(_normalize_record_for_viewer(json.dumps(record)))

    assert [event["event"] for event in normalized["response"]["sse_events"]] == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]
    assert normalized["response"]["body"]["content"] == [{"type": "text", "text": "OK"}]
    assert normalized["response"]["body"]["usage"]["input_tokens"] == 3
    assert normalized["response"]["body"]["usage"]["output_tokens"] == 1
