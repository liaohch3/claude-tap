"""Focused tests for OpenAI Responses support."""

from __future__ import annotations

import json
from pathlib import Path

from claude_tap.sse import SSEReassembler
from claude_tap.viewer import _extract_metadata


def test_sse_reassembler_reconstructs_openai_responses_completed_event() -> None:
    reassembler = SSEReassembler()
    reassembler.feed_bytes(
        b'event: response.created\ndata: {"type":"response.created","response":{"id":"resp_1","status":"in_progress","model":"gpt-5.4"}}\n\n'
    )
    reassembler.feed_bytes(
        b'event: response.completed\ndata: {"type":"response.completed","response":{"id":"resp_1","status":"completed","model":"gpt-5.4","output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":"Hello!"}]},{"type":"reasoning","summary":[{"type":"summary_text","text":""}]}],"usage":{"input_tokens":12,"output_tokens":3,"reasoning_tokens":0}}}\n\n'
    )

    body = reassembler.reconstruct()

    assert body is not None
    assert body["status"] == "completed"
    assert body["output"][0]["content"][0]["text"] == "Hello!"
    assert body["usage"] == {"input_tokens": 12, "output_tokens": 3, "reasoning_tokens": 0}


def test_extract_metadata_supports_responses_input_roles_and_ws_usage() -> None:
    fixture_path = Path(__file__).parent / "fixtures" / "openai_responses_trace.jsonl"
    record_json = fixture_path.read_text(encoding="utf-8").splitlines()[0]

    meta = _extract_metadata(record_json)

    assert meta is not None
    assert meta["message_count"] == 1
    assert meta["input_tokens"] == 500
    assert meta["output_tokens"] == 10
    assert meta["model"] == "gpt-5.4"
    assert meta["has_system"] is True
    assert "exec_command" in meta["tool_names"]


def test_extract_metadata_counts_function_call_and_output_as_messages() -> None:
    """function_call and function_call_output in Responses API input should be counted."""
    fixture_path = Path(__file__).parent / "fixtures" / "responses_function_call_trace.jsonl"
    record_json = fixture_path.read_text(encoding="utf-8").splitlines()[0]

    meta = _extract_metadata(record_json)

    assert meta is not None
    # 1 user + 1 function_call + 1 function_call_output + 1 assistant + 1 function_call + 1 function_call_output = 6
    assert meta["message_count"] == 6
    assert meta["input_tokens"] == 800
    assert meta["output_tokens"] == 20
    assert meta["has_system"] is True
    assert "Read" in meta["tool_names"]
    assert "Edit" in meta["tool_names"]


def test_extract_request_messages_normalizes_function_call_items() -> None:
    """function_call items should be normalized to assistant messages with tool_use content."""
    from claude_tap.viewer import _extract_request_messages

    body = {
        "input": [
            {"role": "user", "content": [{"type": "input_text", "text": "Hello"}]},
            {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "Read", "arguments": "{\"path\": \"/tmp\"}"},
            {"type": "function_call_output", "call_id": "call_1", "output": "file contents here"},
        ]
    }

    msgs = _extract_request_messages(body)

    assert len(msgs) == 3
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"][0]["type"] == "tool_use"
    assert msgs[1]["content"][0]["name"] == "Read"
    assert msgs[2]["role"] == "tool"
    assert msgs[2]["content"] == "file contents here"


def test_extract_metadata_supports_interleaved_responses_roles_without_type() -> None:
    record = {
        "turn": 1,
        "request_id": "req_1",
        "timestamp": "2026-03-17T00:00:00Z",
        "duration_ms": 10,
        "request": {
            "method": "POST",
            "path": "/v1/responses",
            "body": {
                "model": "gpt-5.4",
                "input": [
                    {"role": "developer", "content": [{"type": "input_text", "text": "Follow the repo rules."}]},
                    {"role": "user", "content": [{"type": "input_text", "text": "Fix the bug."}]},
                    {"role": "assistant", "content": [{"type": "output_text", "text": "I will inspect the parser."}]},
                ],
            },
        },
        "response": {"status": 200, "body": {"usage": {"input_tokens": 1, "output_tokens": 1}}},
    }

    meta = _extract_metadata(json.dumps(record))

    assert meta is not None
    assert meta["message_count"] == 3
    assert meta["input_tokens"] == 1
    assert meta["output_tokens"] == 1
