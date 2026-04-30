"""Focused tests for OpenAI Responses support."""

from __future__ import annotations

import json
from pathlib import Path

from claude_tap.sse import SSEReassembler
from claude_tap.viewer import _backfill_responses_history_records, _extract_metadata, _extract_request_messages


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


def test_extract_request_messages_includes_responses_function_history() -> None:
    messages = _extract_request_messages(
        {
            "input": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "I will run a command"}],
                },
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "exec_command",
                    "arguments": '{"cmd":"pwd"}',
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": "/tmp/project",
                },
            ]
        }
    )

    assert [message["role"] for message in messages] == ["assistant", "assistant", "tool"]
    assert messages[1]["content"][0]["type"] == "tool_use"
    assert messages[1]["content"][0]["name"] == "exec_command"
    assert messages[2]["content"][0]["type"] == "tool_result"
    assert messages[2]["content"][0]["content"] == "/tmp/project"


def test_backfill_responses_history_uses_previous_response_id_prefix() -> None:
    records = [
        {
            "turn": 1,
            "request": {
                "path": "/v1/responses",
                "body": {
                    "model": "gpt-5.5",
                    "input": [{"role": "user", "content": [{"type": "input_text", "text": "First prompt"}]}],
                },
            },
            "response": {
                "body": {
                    "id": "resp_1",
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "First answer"}],
                        }
                    ],
                }
            },
        },
        {
            "turn": 2,
            "request": {
                "path": "/v1/responses",
                "body": {
                    "model": "gpt-5.5",
                    "input": [
                        {
                            "type": "function_call_output",
                            "call_id": "call_1",
                            "output": "tool output",
                        }
                    ],
                },
            },
            "response": {"body": {"id": "resp_2", "previous_response_id": "resp_1", "status": "completed"}},
        },
    ]

    _backfill_responses_history_records(records)

    second_input = records[1]["request"]["body"]["input"]
    assert second_input[0]["role"] == "user"
    assert second_input[0]["content"][0]["text"] == "First prompt"
    assert second_input[1]["role"] == "assistant"
    assert second_input[1]["content"][0]["text"] == "First answer"
    assert second_input[2]["type"] == "function_call_output"


def test_backfill_responses_history_uses_same_record_response_chain() -> None:
    records = [
        {
            "turn": 1,
            "request": {
                "path": "/v1/responses",
                "body": {
                    "model": "gpt-5.5",
                    "input": [],
                },
            },
            "response": {
                "status": 101,
                "body": {"id": "resp_2", "previous_response_id": "resp_1", "status": "completed"},
                "ws_events": [
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp_1",
                            "status": "completed",
                            "output": [
                                {
                                    "type": "message",
                                    "role": "assistant",
                                    "content": [{"type": "output_text", "text": "First assistant prefix"}],
                                },
                                {
                                    "type": "function_call",
                                    "call_id": "call_1",
                                    "name": "exec_command",
                                    "arguments": "{}",
                                },
                            ],
                        },
                    },
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp_2",
                            "previous_response_id": "resp_1",
                            "status": "completed",
                            "output": [
                                {
                                    "type": "message",
                                    "role": "assistant",
                                    "content": [{"type": "output_text", "text": "Final answer"}],
                                }
                            ],
                        },
                    },
                ],
            },
        }
    ]

    _backfill_responses_history_records(records)

    first_input = records[0]["request"]["body"]["input"]
    assert first_input[0]["role"] == "assistant"
    assert first_input[0]["content"][0]["text"] == "First assistant prefix"
    assert first_input[1]["type"] == "function_call"


def test_backfill_responses_history_uses_request_ws_user_frames() -> None:
    records = [
        {
            "turn": 1,
            "transport": "websocket",
            "request": {
                "path": "/v1/responses",
                "body": {
                    "type": "response.create",
                    "model": "gpt-5.5",
                    "input": [],
                    "previous_response_id": "resp_prev",
                },
                "ws_events": [
                    {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "Captured user frame"}],
                        },
                    },
                    {
                        "type": "response.create",
                        "response": {
                            "model": "gpt-5.5",
                            "previous_response_id": "resp_prev",
                            "input": [
                                {
                                    "type": "function_call_output",
                                    "call_id": "call_1",
                                    "output": "tool output",
                                }
                            ],
                        },
                    },
                ],
            },
            "response": {"body": {"id": "resp_2", "previous_response_id": "resp_prev"}},
        }
    ]

    _backfill_responses_history_records(records)

    request_input = records[0]["request"]["body"]["input"]
    assert request_input[0]["role"] == "user"
    assert request_input[0]["content"][0]["text"] == "Captured user frame"
    assert request_input[1]["type"] == "function_call_output"
