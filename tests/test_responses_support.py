"""Focused tests for OpenAI Responses support."""

from __future__ import annotations

import json
from pathlib import Path

from claude_tap.sse import SSEReassembler
from claude_tap.viewer import _extract_metadata, _extract_request_messages


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
    assert "web_search" in meta["tool_names"]


def test_extract_metadata_falls_back_to_tool_type_and_nested_function_name() -> None:
    record = {
        "turn": 1,
        "request": {
            "method": "POST",
            "path": "/v1/responses",
            "body": {
                "model": "gpt-5.4",
                "tools": [
                    {"type": "tool_search", "description": "# Tool discovery"},
                    {
                        "type": "function",
                        "function": {
                            "name": "nested_function",
                            "description": "Chat Completions style function tool.",
                        },
                    },
                ],
            },
        },
        "response": {"status": 200, "body": {"usage": {"input_tokens": 1, "output_tokens": 1}}},
    }

    meta = _extract_metadata(json.dumps(record))

    assert meta is not None
    assert "tool_search" in meta["tool_names"]
    assert "nested_function" in meta["tool_names"]


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


def test_extract_metadata_counts_responses_function_call_input_items() -> None:
    record = {
        "turn": 1,
        "request": {
            "method": "POST",
            "path": "/v1/responses",
            "body": {
                "model": "gpt-5.4",
                "instructions": "Use tools when needed.",
                "input": [
                    {"role": "user", "content": [{"type": "input_text", "text": "Read pyproject."}]},
                    {
                        "type": "function_call",
                        "name": "read_file",
                        "arguments": '{"path":"pyproject.toml"}',
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "call_1",
                        "output": '[project]\nname = "claude-tap"',
                    },
                ],
                "tools": [{"type": "function", "name": "read_file"}],
            },
        },
        "response": {"status": 200, "body": {"usage": {"input_tokens": 4, "output_tokens": 2}}},
    }

    meta = _extract_metadata(json.dumps(record))

    assert meta is not None
    assert meta["message_count"] == 3
    assert meta["has_system"] is True
    assert meta["input_tokens"] == 4
    assert meta["output_tokens"] == 2
    assert "read_file" in meta["tool_names"]


def test_extract_request_messages_normalizes_responses_function_call_input_items() -> None:
    messages = _extract_request_messages(
        {
            "input": [
                {"role": "user", "content": [{"type": "input_text", "text": "Read pyproject."}]},
                {
                    "type": "function_call",
                    "name": "read_file",
                    "arguments": '{"path":"pyproject.toml"}',
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": '[project]\nname = "claude-tap"',
                },
                {
                    "type": "function_call",
                    "name": "shell",
                    "arguments": "not json",
                },
                {
                    "type": "function_call",
                    "name": "missing_args",
                },
            ]
        }
    )

    assert messages[0]["role"] == "user"
    assert messages[1] == {
        "role": "assistant",
        "content": [{"type": "tool_use", "name": "read_file", "input": {"path": "pyproject.toml"}}],
    }
    assert messages[2] == {"role": "tool", "content": '[project]\nname = "claude-tap"'}
    assert messages[3]["content"][0]["input"] == "not json"
    assert messages[4]["content"][0]["input"] == {}


def test_extract_metadata_ignores_list_response_body() -> None:
    record = {
        "turn": 1,
        "request_id": "req_1",
        "timestamp": "2026-03-17T00:00:00Z",
        "duration_ms": 10,
        "request": {
            "method": "POST",
            "path": "/v1/messages",
            "body": {
                "model": "claude-opus-4-6",
                "messages": [{"role": "user", "content": "hello"}],
            },
        },
        "response": {"status": 200, "body": [{"type": "text", "text": "hello"}]},
    }

    meta = _extract_metadata(json.dumps(record))

    assert meta is not None
    assert meta["message_count"] == 1
    assert meta["input_tokens"] == 0
    assert meta["output_tokens"] == 0
    assert meta["error_message"] == ""
