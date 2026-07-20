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


def test_sse_reassembler_recovers_output_from_item_events_when_completed_is_empty() -> None:
    # The Codex/ChatGPT backend over HTTP/SSE (model_provider = "openai_http")
    # streams output via response.output_item.* events and sends a terminal
    # response.completed with output: []. The reassembler must keep the items.
    reassembler = SSEReassembler()
    for frame in (
        b'event: response.created\ndata: {"type":"response.created","response":{"id":"resp_1","status":"in_progress","model":"gpt-5.5","output":[]}}\n\n',
        b'event: response.output_item.added\ndata: {"type":"response.output_item.added","output_index":0,"item":{"type":"message","role":"assistant","content":[]}}\n\n',
        b'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","output_index":0,"delta":"Hello"}\n\n',
        b'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","output_index":0,"delta":" world"}\n\n',
        b'event: response.output_item.done\ndata: {"type":"response.output_item.done","output_index":0,"item":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"Hello world"}]}}\n\n',
        b'event: response.completed\ndata: {"type":"response.completed","response":{"id":"resp_1","status":"completed","model":"gpt-5.5","output":[],"usage":{"input_tokens":13159,"output_tokens":18,"total_tokens":13177}}}\n\n',
    ):
        reassembler.feed_bytes(frame)

    body = reassembler.reconstruct()

    assert body is not None
    assert body["status"] == "completed"
    assert body["output"][0]["content"][0]["text"] == "Hello world"
    assert body["usage"] == {"input_tokens": 13159, "output_tokens": 18, "total_tokens": 13177}


def test_sse_reassembler_keeps_streamed_text_when_completed_done_is_missing() -> None:
    # A truncated capture: output_item.done / response.completed never arrive,
    # so the accumulated output_text.delta content must survive on its own.
    reassembler = SSEReassembler()
    for frame in (
        b'event: response.created\ndata: {"type":"response.created","response":{"id":"resp_2","status":"in_progress","output":[]}}\n\n',
        b'event: response.output_item.added\ndata: {"type":"response.output_item.added","output_index":0,"item":{"type":"message","role":"assistant","content":[]}}\n\n',
        b'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","output_index":0,"delta":"partial"}\n\n',
    ):
        reassembler.feed_bytes(frame)

    body = reassembler.reconstruct()

    assert body is not None
    assert body["output"][0]["content"][0]["text"] == "partial"


def test_sse_reassembler_keeps_streamed_function_arguments_when_done_is_missing() -> None:
    reassembler = SSEReassembler()
    for frame in (
        b'event: response.output_item.added\ndata: {"type":"response.output_item.added","output_index":0,"item":{"type":"function_call","name":"read_file","arguments":""}}\n\n',
        b'event: response.function_call_arguments.delta\ndata: {"type":"response.function_call_arguments.delta","output_index":0,"delta":"{\\"path\\":"}\n\n',
        b'event: response.function_call_arguments.delta\ndata: {"type":"response.function_call_arguments.delta","output_index":0,"delta":"\\"README.md\\"}"}\n\n',
    ):
        reassembler.feed_bytes(frame)

    body = reassembler.reconstruct()

    assert body is not None
    assert body["output"][0]["arguments"] == '{"path":"README.md"}'


def test_sse_reassembler_keeps_streamed_custom_tool_input_when_done_is_missing() -> None:
    reassembler = SSEReassembler()
    for frame in (
        b'event: response.output_item.added\ndata: {"type":"response.output_item.added","output_index":0,"item":{"type":"custom_tool_call","name":"shell","input":""}}\n\n',
        b'event: response.custom_tool_call_input.delta\ndata: {"type":"response.custom_tool_call_input.delta","output_index":0,"delta":"pw"}\n\n',
        b'event: response.custom_tool_call_input.delta\ndata: {"type":"response.custom_tool_call_input.delta","output_index":0,"delta":"d"}\n\n',
    ):
        reassembler.feed_bytes(frame)

    body = reassembler.reconstruct()

    assert body is not None
    assert body["output"][0]["input"] == "pwd"


def test_sse_reassembler_merges_response_incomplete_terminal_event() -> None:
    # A response truncated by max_output_tokens ends with response.incomplete,
    # not response.completed. The final status/usage/details must be captured
    # while the streamed output items are preserved.
    reassembler = SSEReassembler()
    for frame in (
        b'event: response.created\ndata: {"type":"response.created","response":{"id":"r","status":"in_progress","output":[]}}\n\n',
        b'event: response.output_item.done\ndata: {"type":"response.output_item.done","output_index":0,"item":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"cut off"}]}}\n\n',
        b'event: response.incomplete\ndata: {"type":"response.incomplete","response":{"id":"r","status":"incomplete","output":[],"incomplete_details":{"reason":"max_output_tokens"},"usage":{"input_tokens":5,"output_tokens":7}}}\n\n',
    ):
        reassembler.feed_bytes(frame)

    body = reassembler.reconstruct()

    assert body is not None
    assert body["status"] == "incomplete"
    assert body["incomplete_details"] == {"reason": "max_output_tokens"}
    assert body["usage"] == {"input_tokens": 5, "output_tokens": 7}
    assert body["output"][0]["content"][0]["text"] == "cut off"


def test_sse_reassembler_merges_response_failed_terminal_event() -> None:
    reassembler = SSEReassembler()
    for frame in (
        b'event: response.created\ndata: {"type":"response.created","response":{"id":"r","status":"in_progress","output":[]}}\n\n',
        b'event: response.failed\ndata: {"type":"response.failed","response":{"id":"r","status":"failed","output":[],"error":{"code":"server_error","message":"boom"},"usage":{"input_tokens":3,"output_tokens":0}}}\n\n',
    ):
        reassembler.feed_bytes(frame)

    body = reassembler.reconstruct()

    assert body is not None
    assert body["status"] == "failed"
    assert body["error"] == {"code": "server_error", "message": "boom"}


def test_sse_reassembler_records_stream_level_response_error_event() -> None:
    # response.error has no "response" object and can arrive mid-stream; the
    # error must be attached without discarding accumulated output.
    reassembler = SSEReassembler()
    for frame in (
        b'event: response.created\ndata: {"type":"response.created","response":{"id":"r","status":"in_progress","output":[]}}\n\n',
        b'event: response.output_item.done\ndata: {"type":"response.output_item.done","output_index":0,"item":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"hi"}]}}\n\n',
        b'event: response.error\ndata: {"type":"error","code":"rate_limit_exceeded","message":"slow down","param":null}\n\n',
    ):
        reassembler.feed_bytes(frame)

    body = reassembler.reconstruct()

    assert body is not None
    assert body["status"] == "failed"
    assert body["error"]["code"] == "rate_limit_exceeded"
    assert body["error"]["message"] == "slow down"
    assert body["output"][0]["content"][0]["text"] == "hi"


def test_sse_reassembler_accepts_output_item_before_response_created() -> None:
    reassembler = SSEReassembler()
    reassembler.feed_bytes(
        b'event: response.output_item.done\ndata: {"type":"response.output_item.done","output_index":"bad","item":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"early"}]}}\n\n'
    )

    body = reassembler.reconstruct()

    assert body is not None
    assert body["output"][0]["content"][0]["text"] == "early"


def test_sse_reassembler_repairs_non_list_responses_output_and_content() -> None:
    reassembler = SSEReassembler()
    for frame in (
        b'event: response.created\ndata: {"type":"response.created","response":{"id":"r","status":"in_progress","output":{"unexpected":true}}}\n\n',
        b'event: response.output_item.added\ndata: {"type":"response.output_item.added","output_index":0,"item":{"type":"message","role":"assistant","content":{"unexpected":true}}}\n\n',
        b'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"repaired"}\n\n',
    ):
        reassembler.feed_bytes(frame)

    body = reassembler.reconstruct()

    assert body is not None
    assert body["output"][0]["content"] == [{"type": "output_text", "text": "repaired"}]


def test_sse_reassembler_ignores_malformed_responses_item_and_text_events() -> None:
    item_reassembler = SSEReassembler()
    item_reassembler.feed_bytes(
        b'event: response.output_item.done\ndata: {"type":"response.output_item.done","output_index":0,"item":null}\n\n'
    )
    assert item_reassembler.reconstruct() is None

    empty_delta_reassembler = SSEReassembler()
    empty_delta_reassembler.feed_bytes(
        b'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","output_index":0,"delta":""}\n\n'
    )
    assert empty_delta_reassembler.reconstruct() is None

    out_of_range_reassembler = SSEReassembler()
    out_of_range_reassembler.feed_bytes(
        b'event: response.created\ndata: {"type":"response.created","response":{"id":"r","output":[]}}\n\n'
    )
    out_of_range_reassembler.feed_bytes(
        b'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","output_index":0,"delta":"ignored"}\n\n'
    )
    assert out_of_range_reassembler.reconstruct() == {"id": "r", "output": []}

    non_dict_item_reassembler = SSEReassembler()
    non_dict_item_reassembler.feed_bytes(
        b'event: response.created\ndata: {"type":"response.created","response":{"id":"r","output":["raw"]}}\n\n'
    )
    non_dict_item_reassembler.feed_bytes(
        b'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","output_index":0,"delta":"ignored"}\n\n'
    )
    assert non_dict_item_reassembler.reconstruct() == {"id": "r", "output": ["raw"]}


def test_sse_reassembler_handles_responses_terminal_without_response_object() -> None:
    reassembler = SSEReassembler()
    reassembler.feed_bytes(b'event: response.completed\ndata: {"type":"response.completed","id":"r"}\n\n')

    body = reassembler.reconstruct()

    assert body == {"type": "response.completed", "id": "r"}


def test_sse_reassembler_records_response_error_before_any_snapshot() -> None:
    reassembler = SSEReassembler()
    reassembler.feed_bytes(b'event: response.error\ndata: {"type":"error","code":"server_error","message":"boom"}\n\n')

    body = reassembler.reconstruct()

    assert body is not None
    assert body["status"] == "failed"
    assert body["error"] == {"code": "server_error", "message": "boom"}


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


def test_extract_metadata_maps_responses_cached_tokens_to_cache_read() -> None:
    record = {
        "turn": 1,
        "request": {
            "method": "POST",
            "path": "/responses",
            "body": {"model": "gpt-5.4", "input": [{"role": "user", "content": "hi"}]},
        },
        "response": {
            "status": 200,
            "body": {
                "usage": {
                    "input_tokens": 11767,
                    "input_tokens_details": {"cached_tokens": 11648},
                    "output_tokens": 6,
                    "total_tokens": 11773,
                }
            },
        },
    }

    meta = _extract_metadata(json.dumps(record))

    assert meta is not None
    assert meta["input_tokens"] == 11767
    assert meta["output_tokens"] == 6
    assert meta["cache_read_input_tokens"] == 11648


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


def test_extract_metadata_counts_responses_tool_search_call_from_body_output() -> None:
    record = {
        "turn": 1,
        "request": {
            "method": "POST",
            "path": "/v1/responses",
            "body": {"model": "gpt-5.5", "input": [{"role": "user", "content": "Find tools."}]},
        },
        "response": {
            "status": 200,
            "body": {
                "output": [
                    {
                        "type": "tool_search_call",
                        "status": "completed",
                        "arguments": {"query": "browser automation", "limit": 5},
                        "call_id": "call_search",
                        "execution": "client",
                    }
                ],
                "usage": {"input_tokens": 4, "output_tokens": 2},
            },
        },
    }

    meta = _extract_metadata(json.dumps(record))

    assert meta is not None
    assert meta["response_tool_names"] == ["tool_search"]


def test_extract_metadata_counts_generic_responses_tool_call_items() -> None:
    record = {
        "turn": 1,
        "request": {
            "method": "POST",
            "path": "/v1/responses",
            "body": {"model": "gpt-5.5", "input": [{"role": "user", "content": "Search."}]},
        },
        "response": {
            "status": 200,
            "body": {
                "output": [
                    {"type": "web_search_call", "status": "completed", "action": {"type": "search", "query": "docs"}},
                    {"type": "file_search_call", "status": "completed", "queries": ["parser"]},
                    {"type": "custom_tool_call", "status": "completed", "name": "deploy_preview"},
                ],
                "usage": {"input_tokens": 4, "output_tokens": 2},
            },
        },
    }

    meta = _extract_metadata(json.dumps(record))

    assert meta is not None
    assert meta["response_tool_names"] == ["web_search", "file_search", "deploy_preview"]


def test_extract_metadata_counts_ws_tool_search_call_output_item_when_completed_output_is_empty() -> None:
    record = {
        "turn": 1,
        "request": {
            "method": "WEBSOCKET",
            "path": "/backend-api/codex/responses",
            "body": {"model": "gpt-5.5", "input": [{"role": "user", "content": "Find tools."}]},
        },
        "response": {
            "status": 101,
            "body": {"output": [], "usage": {"input_tokens": 4, "output_tokens": 2}},
            "ws_events": [
                {"type": "response.created", "response": {"id": "resp_search", "status": "in_progress"}},
                {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": {
                        "type": "tool_search_call",
                        "status": "completed",
                        "arguments": {"query": "browser automation", "limit": 5},
                        "call_id": "call_search",
                        "execution": "client",
                    },
                },
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_search",
                        "status": "completed",
                        "output": [],
                        "usage": {"input_tokens": 4, "output_tokens": 2},
                    },
                },
            ],
        },
    }

    meta = _extract_metadata(json.dumps(record))

    assert meta is not None
    assert meta["response_tool_names"] == ["tool_search"]


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
                {
                    "type": "web_search_call",
                    "action": {"type": "search", "query": "Responses items"},
                },
                {
                    "type": "computer_call_output",
                    "call_id": "call_screen",
                    "output": {"type": "computer_screenshot", "image_url": "https://example.test/screen.png"},
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
    assert messages[5] == {
        "role": "assistant",
        "content": [
            {
                "type": "tool_use",
                "name": "web_search",
                "input": {"action": {"type": "search", "query": "Responses items"}},
            }
        ],
    }
    assert messages[6]["role"] == "tool"
    assert "computer_screenshot" in messages[6]["content"]


def test_extract_request_messages_normalizes_responses_tool_search_output_input_items() -> None:
    messages = _extract_request_messages(
        {
            "input": [
                {
                    "type": "tool_search_output",
                    "call_id": "call_search",
                    "status": "completed",
                    "execution": "client",
                    "tools": [
                        {
                            "type": "namespace",
                            "name": "mcp__codex_apps__figma",
                            "tools": [{"type": "function", "name": "_use_figma"}],
                        }
                    ],
                }
            ]
        }
    )

    assert len(messages) == 1
    assert messages[0]["role"] == "tool"
    assert "tool_search_output" in messages[0]["content"]
    assert "mcp__codex_apps__figma" in messages[0]["content"]
    assert "mcp__codex_apps__figma._use_figma" in messages[0]["content"]


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
