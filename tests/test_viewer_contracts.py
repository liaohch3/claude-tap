"""Cross-client contract tests for the self-contained HTML viewer.

These tests intentionally exercise viewer.html through a real browser instead
of only checking generated markup. The goal is to keep core semantic sections
stable across supported trace shapes whenever the large inline JS file changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from claude_tap.compact_trace import build_compact_trace_bundle
from claude_tap.viewer import _generate_html_viewer, _generate_html_viewer_from_compact_bundle, _read_viewer_template

pw_missing = False
try:
    from playwright.sync_api import Page, sync_playwright  # noqa: F401
except ImportError:
    pw_missing = True
    Page = Any  # type: ignore[assignment,misc]

pytestmark = pytest.mark.skipif(pw_missing, reason="playwright not installed")


@dataclass(frozen=True)
class ViewerContractCase:
    name: str
    records: tuple[dict[str, Any], ...]
    expected_sections: tuple[str, ...]
    expected_system: str | None
    expected_roles: tuple[str, ...]
    expected_tools: tuple[str, ...]
    expected_output_types: tuple[str, ...]
    expected_usage: dict[str, int]
    required_detail_text: tuple[str, ...]
    min_stream_events: int = 0
    entry_index: int = 0
    expected_sidebar_label: str | None = None


def _sse_frame(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _anthropic_messages_record() -> dict[str, Any]:
    return {
        "timestamp": "2026-05-13T13:20:00+00:00",
        "request_id": "req_anthropic_contract",
        "turn": 1,
        "duration_ms": 100,
        "request": {
            "method": "POST",
            "path": "/v1/messages",
            "headers": {},
            "body": {
                "model": "claude-opus-4-6",
                "system": "Claude Code contract system prompt.",
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": "Read pyproject.toml."}]},
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_read",
                                "name": "Read",
                                "input": {"file_path": "pyproject.toml"},
                            }
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_read",
                                "content": "project metadata",
                            }
                        ],
                    },
                ],
                "tools": [
                    {
                        "name": "Read",
                        "description": "Read a file.",
                        "input_schema": {
                            "type": "object",
                            "properties": {"file_path": {"type": "string"}},
                            "required": ["file_path"],
                        },
                    }
                ],
            },
        },
        "response": {
            "status": 200,
            "headers": {},
            "body": {
                "content": [{"type": "text", "text": "Anthropic response OK."}],
                "usage": {"input_tokens": 120, "output_tokens": 9, "cache_read_input_tokens": 40},
            },
        },
    }


def _tool_bloat_records() -> tuple[dict[str, Any], ...]:
    """Two Anthropic turns: the first ordinary, the second carrying an oversized
    tool result.

    Both belong to the shared contract corpus so the sidebar badge and the detail
    banner render during the coverage sweep as well as in the dedicated
    assertions below.
    """
    large_tool_output = "A" * 25000
    tools = [
        {
            "name": "grep",
            "description": "Search files.",
            "input_schema": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}},
                "required": ["pattern"],
            },
        }
    ]
    return (
        {
            "timestamp": "2026-08-14T10:00:00+00:00",
            "request_id": "req_tool_bloat_1",
            "turn": 1,
            "duration_ms": 1500,
            "request": {
                "method": "POST",
                "path": "/v1/messages",
                "headers": {},
                "body": {
                    "model": "claude-opus-5",
                    "system": "Bloat contract system prompt.",
                    "messages": [{"role": "user", "content": [{"type": "text", "text": "Grep the sources."}]}],
                    "tools": tools,
                },
            },
            "response": {
                "status": 200,
                "headers": {},
                "body": {
                    "content": [{"type": "text", "text": "Bloat contract OK."}],
                    "usage": {"input_tokens": 100, "output_tokens": 50},
                },
            },
        },
        {
            "timestamp": "2026-08-14T10:00:10+00:00",
            "request_id": "req_tool_bloat_2",
            "turn": 2,
            "duration_ms": 2000,
            "request": {
                "method": "POST",
                "path": "/v1/messages",
                "headers": {},
                "body": {
                    "model": "claude-opus-5",
                    "system": "Bloat contract system prompt.",
                    "messages": [
                        {"role": "user", "content": [{"type": "text", "text": "Grep the sources."}]},
                        {
                            "role": "assistant",
                            "content": [
                                {"type": "tool_use", "id": "toolu_grep", "name": "grep", "input": {"pattern": "x"}}
                            ],
                        },
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "toolu_grep",
                                    "content": large_tool_output,
                                }
                            ],
                        },
                    ],
                    "tools": tools,
                },
            },
            "response": {
                "status": 200,
                "headers": {},
                "body": {
                    "content": [{"type": "text", "text": "Analyzed grep result."}],
                    "usage": {"input_tokens": 200, "output_tokens": 100},
                },
            },
        },
        {
            "timestamp": "2026-08-14T10:00:20+00:00",
            "request_id": "req_tool_bloat_3",
            "turn": 3,
            "duration_ms": 2500,
            "request": {
                "method": "POST",
                "path": "/v1/messages",
                "headers": {},
                "body": {
                    "model": "claude-opus-5",
                    "system": "Bloat contract system prompt.",
                    "messages": [
                        {"role": "user", "content": [{"type": "text", "text": "Screenshot the page."}]},
                        {
                            "role": "assistant",
                            "content": [
                                {"type": "tool_use", "id": "toolu_shot", "name": "grep", "input": {"pattern": "y"}}
                            ],
                        },
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "toolu_shot",
                                    # An image is billed by dimension, so its base64 is not
                                    # result text and must leave the row unbadged however
                                    # far past the byte threshold it runs.
                                    "content": [
                                        {
                                            "type": "image",
                                            "source": {
                                                "type": "base64",
                                                "media_type": "image/png",
                                                "data": "B" * 25000,
                                            },
                                        }
                                    ],
                                }
                            ],
                        },
                    ],
                    "tools": tools,
                },
            },
            "response": {
                "status": 200,
                "headers": {},
                "body": {
                    "content": [{"type": "text", "text": "Described the screenshot."}],
                    "usage": {"input_tokens": 300, "output_tokens": 120},
                },
            },
        },
        {
            "timestamp": "2026-08-14T10:00:30+00:00",
            "request_id": "req_tool_bloat_4",
            "turn": 4,
            "duration_ms": 2600,
            "request": {
                "method": "POST",
                "path": "/v1/responses",
                "headers": {},
                "body": {
                    "model": "gpt-5.4",
                    "instructions": "Bloat contract system prompt.",
                    "input": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "Screenshot the viewport."}],
                        },
                        {
                            "type": "computer_call_output",
                            "call_id": "call_shot",
                            # A computer-use call hands its screenshot back as a
                            # `computer_screenshot` output.  Serializing that would
                            # show a wall of base64 and count an encoded image as
                            # result text, so the viewer normalizes it to an image
                            # block on both sides of the detector.
                            "output": {
                                "type": "computer_screenshot",
                                "image_url": "data:image/png;base64," + "C" * 25000,
                            },
                        },
                    ],
                    "tools": [{"type": "function", "name": "grep", "description": "Search files."}],
                },
            },
            "response": {
                "status": 200,
                "headers": {},
                "body": {
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "Read the screenshot."}],
                        }
                    ],
                    "usage": {"input_tokens": 320, "output_tokens": 130},
                },
            },
        },
    )


def _responses_record() -> dict[str, Any]:
    return {
        "timestamp": "2026-05-13T13:21:00+00:00",
        "request_id": "req_responses_contract",
        "turn": 1,
        "duration_ms": 100,
        "request": {
            "method": "POST",
            "path": "/v1/responses",
            "headers": {},
            "body": {
                "model": "gpt-5.4",
                "instructions": "You are Codex contract system prompt.",
                "input": [
                    {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "Run pwd."}],
                    }
                ],
                "tools": [{"type": "function", "name": "exec_command", "description": "Runs a command."}],
            },
        },
        "response": {
            "status": 200,
            "headers": {},
            "body": {
                "output": [
                    {
                        "type": "function_call",
                        "name": "exec_command",
                        "arguments": '{"cmd":"pwd"}',
                        "call_id": "call_pwd",
                    },
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "Responses final OK."}],
                    },
                ],
                "usage": {
                    "input_tokens": 130,
                    "output_tokens": 14,
                    "input_tokens_details": {"cached_tokens": 50},
                },
            },
        },
    }


def _responses_empty_input_record() -> dict[str, Any]:
    record = _responses_record()
    record["request_id"] = "req_responses_empty_input"
    record["request"]["body"]["input"] = []
    record["response"]["body"]["output"] = []
    return record


def _codex_websocket_record() -> dict[str, Any]:
    return {
        "timestamp": "2026-05-13T13:22:00+00:00",
        "request_id": "req_codex_ws_contract",
        "turn": "1.2",
        "duration_ms": 100,
        "transport": "websocket",
        "request": {
            "method": "WEBSOCKET",
            "path": "/backend-api/codex/responses",
            "headers": {},
            "body": {
                "type": "response.create",
                "model": "gpt-5.5",
                "instructions": "You are Codex WebSocket contract system prompt.",
                "input": [
                    {
                        "type": "message",
                        "role": "developer",
                        "content": [{"type": "input_text", "text": "Follow repository rules."}],
                    },
                    {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "Continue after a tool call."}],
                    },
                ],
                "tools": [{"type": "function", "name": "exec_command", "description": "Runs a command."}],
                "stream": True,
            },
        },
        "response": {
            "status": 101,
            "headers": {},
            "body": None,
            "ws_events": [
                {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": {
                        "type": "function_call",
                        "name": "exec_command",
                        "arguments": '{"cmd":"pwd"}',
                        "call_id": "call_pwd",
                    },
                },
                {
                    "type": "response.output_item.done",
                    "output_index": 1,
                    "item": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "WebSocket final OK."}],
                    },
                },
                {
                    "type": "response.completed",
                    "response": {
                        "usage": {"input_tokens": 140, "output_tokens": 8, "total_tokens": 148},
                        "output": [],
                    },
                },
            ],
        },
    }


def _content_block_boundary_record() -> dict[str, Any]:
    return {
        "timestamp": "2026-05-24T07:20:00+00:00",
        "request_id": "req_content_block_boundary_contract",
        "turn": 99,
        "duration_ms": 120,
        "request": {
            "method": "POST",
            "path": "/v1/responses",
            "headers": {},
            "body": {
                "model": "zz-content-block-boundary",
                "system": [
                    {"type": "text", "text": "System block one."},
                    {"type": "text", "text": "System block two."},
                ],
                "input": [
                    {
                        "type": "message",
                        "role": "developer",
                        "content": [
                            {"type": "input_text", "text": "Developer block one."},
                            {"type": "input_text", "text": "Developer block two."},
                        ],
                    },
                    {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "User block one."},
                            {"type": "input_text", "text": "User block two."},
                            {
                                "type": "input_image",
                                "image_url": (
                                    "data:image/png;base64,"
                                    "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAYAAADED76LAAAAEklEQVR4nGPQb3j7Hx9mGBkKAKVjpsHoKJzJAAAAAElFTkSuQmCC"
                                ),
                                "detail": "high",
                            },
                        ],
                    },
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": "Assistant text block."},
                            {"type": "tool_use", "name": "read_file", "input": {"path": "README.md"}},
                        ],
                    },
                    {
                        "type": "message",
                        "role": "tool",
                        "content": [
                            {"type": "tool_result", "tool_use_id": "call_one", "content": "Tool result one."},
                            {"type": "tool_result", "tool_use_id": "call_two", "content": "Tool result two."},
                        ],
                    },
                ],
            },
        },
        "response": {
            "status": 200,
            "headers": {},
            "body": {
                "content": [{"type": "text", "text": "Content block response OK."}],
                "usage": {"input_tokens": 42, "output_tokens": 5},
            },
        },
    }


def _codex_reverse_websocket_record() -> dict[str, Any]:
    record = _codex_websocket_record()
    record["request_id"] = "req_codex_reverse_ws_contract"
    record["request"]["path"] = "/v1/responses"
    return record


def _chat_completions_record() -> dict[str, Any]:
    return {
        "timestamp": "2026-05-13T13:23:00+00:00",
        "request_id": "req_chat_contract",
        "turn": 1,
        "duration_ms": 100,
        "request": {
            "method": "POST",
            "path": "/chat/completions",
            "headers": {},
            "body": {
                "model": "kimi-k2-turbo-preview",
                "messages": [
                    {"role": "system", "content": "Kimi contract system prompt."},
                    {"role": "user", "content": "Read the project metadata."},
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_read",
                                "type": "function",
                                "function": {"name": "read_file", "arguments": '{"path":"pyproject.toml"}'},
                            }
                        ],
                    },
                    {"role": "tool", "tool_call_id": "call_read", "content": "project metadata"},
                ],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "description": "Read a file.",
                            "parameters": {
                                "type": "object",
                                "properties": {"path": {"type": "string"}},
                            },
                        },
                    }
                ],
            },
        },
        "response": {
            "status": 200,
            "headers": {},
            "body": {
                "id": "chatcmpl-contract",
                "object": "chat.completion",
                "model": "kimi-k2-turbo-preview",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "reasoning_content": "Need to inspect metadata before answering.",
                            "tool_calls": [
                                {
                                    "id": "call_response_read",
                                    "type": "function",
                                    "function": {
                                        "name": "inspect_metadata",
                                        "arguments": '{"path":"pyproject.toml"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    },
                    {
                        "index": 1,
                        "message": {"role": "assistant", "content": "Chat final OK."},
                        "finish_reason": "stop",
                    },
                ],
                "usage": {"prompt_tokens": 150, "completion_tokens": 10, "cached_tokens": 70},
            },
        },
    }


def _opencode_chat_completions_record() -> dict[str, Any]:
    return {
        "timestamp": "2026-05-13T16:11:10+00:00",
        "request_id": "req_opencode_real_shape_contract",
        "turn": 2,
        "duration_ms": 1800,
        "request": {
            "method": "POST",
            "path": "/zen/v1/chat/completions",
            "headers": {"Host": "opencode.ai"},
            "body": {
                "model": "deepseek-v4-flash-free",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are opencode, an interactive CLI tool that helps users "
                            "with software engineering tasks."
                        ),
                    },
                    {
                        "role": "user",
                        "content": "Run printf OPENCODE_TOOL_ONE and pwd.",
                    },
                    {
                        "role": "assistant",
                        "content": "",
                        "reasoning_content": "The user wants me to run a specific command.",
                        "tool_calls": [
                            {
                                "id": "call_one",
                                "type": "function",
                                "function": {
                                    "name": "bash",
                                    "arguments": (
                                        '{"command":"printf \'OPENCODE_TOOL_ONE\\\\n\'; pwd",'
                                        '"description":"Run printf and pwd"}'
                                    ),
                                },
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "call_one",
                        "content": "OPENCODE_TOOL_ONE\n/home/liaohch3/src/github.com/liaohch3/claude-tap-3\n",
                    },
                    {
                        "role": "assistant",
                        "content": "OPENCODE_TOOL_ONE\n/home/liaohch3/src/github.com/liaohch3/claude-tap-3",
                    },
                    {
                        "role": "user",
                        "content": "Second turn: run printf OPENCODE_TOOL_TWO and ls pyproject.toml.",
                    },
                    {
                        "role": "assistant",
                        "content": "",
                        "reasoning_content": "The user wants a second command and the previous path.",
                        "tool_calls": [
                            {
                                "id": "call_two",
                                "type": "function",
                                "function": {
                                    "name": "bash",
                                    "arguments": (
                                        '{"command":"printf \'OPENCODE_TOOL_TWO\\\\n\'; ls pyproject.toml",'
                                        '"description":"Run printf and ls"}'
                                    ),
                                },
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "call_two",
                        "content": "OPENCODE_TOOL_TWO\npyproject.toml\n",
                    },
                ],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "bash",
                            "description": "Executes a given bash command.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "command": {"type": "string"},
                                    "description": {"type": "string"},
                                },
                                "required": ["command"],
                            },
                        },
                    },
                    {
                        "type": "function",
                        "function": {
                            "name": "edit",
                            "description": "Edit a file.",
                            "parameters": {
                                "type": "object",
                                "properties": {"filePath": {"type": "string"}},
                            },
                        },
                    },
                ],
                "stream": True,
                "stream_options": {"include_usage": True},
                "tool_choice": "auto",
            },
        },
        "response": {
            "status": 200,
            "headers": {},
            "body": {
                "object": "chat.completion",
                "model": "deepseek-v4-flash",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "The user wants both the path from the previous output and the new output.",
                    },
                    {
                        "type": "text",
                        "text": (
                            "Path from OPENCODE_TOOL_ONE: "
                            "`/home/liaohch3/src/github.com/liaohch3/claude-tap-3`\n\n"
                            "New command output:\n```\nOPENCODE_TOOL_TWO\npyproject.toml\n```"
                        ),
                    },
                ],
                "usage": {
                    "prompt_tokens": 14070,
                    "completion_tokens": 109,
                    "total_tokens": 14179,
                    "prompt_tokens_details": {"cached_tokens": 13952},
                    "completion_tokens_details": {"reasoning_tokens": 57},
                    "input_tokens": 14070,
                    "output_tokens": 109,
                    "cache_read_input_tokens": 13952,
                },
            },
            "sse_events": [
                {
                    "event": "message",
                    "data": {
                        "choices": [
                            {
                                "delta": {
                                    "reasoning_content": (
                                        "The user wants both the path from the previous output and the new output."
                                    )
                                }
                            }
                        ]
                    },
                },
                {
                    "event": "message",
                    "data": {
                        "choices": [
                            {
                                "delta": {
                                    "content": (
                                        "Path from OPENCODE_TOOL_ONE: "
                                        "`/home/liaohch3/src/github.com/liaohch3/claude-tap-3`"
                                    )
                                }
                            }
                        ]
                    },
                },
            ],
        },
    }


def _opencode_openai_oauth_responses_record() -> dict[str, Any]:
    return {
        "timestamp": "2026-05-13T16:45:43+00:00",
        "request_id": "req_opencode_openai_oauth_responses_contract",
        "turn": 3,
        "duration_ms": 6106,
        "request": {
            "method": "POST",
            "path": "/backend-api/codex/responses",
            "headers": {"Host": "chatgpt.com", "User-Agent": "opencode/1.14.48"},
            "body": {
                "model": "gpt-5.4-mini",
                "instructions": (
                    "You are OpenCode, You and the user share the same workspace "
                    "and collaborate to achieve the user's goals."
                ),
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": (
                                    "Use the bash tool to run exactly: printf "
                                    "'OPENCODE_OPENAI_OAUTH_TOOL_ONE\\n'; pwd ."
                                ),
                            }
                        ],
                    },
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "Running the requested shell command."}],
                    },
                    {
                        "type": "function_call",
                        "call_id": "call_oauth_bash",
                        "name": "bash",
                        "arguments": (
                            '{"command":"printf \'OPENCODE_OPENAI_OAUTH_TOOL_ONE\\\\n\'; pwd .",'
                            '"description":"Runs the requested command"}'
                        ),
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "call_oauth_bash",
                        "output": (
                            "OPENCODE_OPENAI_OAUTH_TOOL_ONE\n/home/liaohch3/src/github.com/liaohch3/claude-tap-3\n"
                        ),
                    },
                ],
                "tools": [
                    {"type": "function", "name": "bash", "description": "Run a shell command."},
                    {"type": "function", "name": "read", "description": "Read a file."},
                ],
                "stream": True,
            },
        },
        "response": {
            "status": 200,
            "headers": {},
            "body": {
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": (
                                    "OPENCODE_OPENAI_OAUTH_TOOL_ONE\n"
                                    "/home/liaohch3/src/github.com/liaohch3/claude-tap-3"
                                ),
                            }
                        ],
                    }
                ],
                "usage": {
                    "input_tokens": 12424,
                    "output_tokens": 115,
                    "input_tokens_details": {"cached_tokens": 11776},
                },
            },
            "sse_events": [
                {
                    "event": "response.output_item.done",
                    "data": {
                        "type": "response.output_item.done",
                        "output_index": 0,
                        "item": {
                            "type": "message",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": (
                                        "OPENCODE_OPENAI_OAUTH_TOOL_ONE\n"
                                        "/home/liaohch3/src/github.com/liaohch3/claude-tap-3"
                                    ),
                                }
                            ],
                        },
                    },
                }
            ],
        },
    }


def _pi_openai_oauth_websocket_record() -> dict[str, Any]:
    system_prompt = (
        "You are an expert coding assistant operating inside pi, a coding agent harness. "
        "You help users by reading files, executing commands, editing code, and writing new files.\n\n"
        "Available tools:\n- bash: Execute bash commands (ls, grep, find, etc.)"
    )
    initial_request = {
        "type": "response.create",
        "model": "gpt-5.3-codex-spark",
        "store": False,
        "stream": True,
        "instructions": system_prompt,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "Use the bash tool to run exactly: printf 'PI_TOOL_ONE\\n'; pwd.",
                    }
                ],
            }
        ],
        "tools": [{"type": "function", "name": "bash", "description": "Execute a bash command."}],
        "reasoning": {"effort": "low", "summary": "auto"},
        "prompt_cache_key": "session-pi-contract",
        "tool_choice": "auto",
        "parallel_tool_calls": True,
    }
    continuation_request = {
        **initial_request,
        "previous_response_id": "resp_pi_tool_call",
        "input": [
            {
                "type": "function_call_output",
                "call_id": "call_pi_bash",
                "output": "PI_TOOL_ONE\n/home/liaohch3/src/github.com/liaohch3/claude-tap-3\n",
            }
        ],
    }
    return {
        "timestamp": "2026-05-14T07:47:21+00:00",
        "request_id": "req_pi_openai_oauth_ws_contract",
        "turn": 1,
        "duration_ms": 2400,
        "transport": "websocket",
        "request": {
            "method": "WEBSOCKET",
            "path": "/backend-api/codex/responses",
            "headers": {"Host": "chatgpt.com", "User-Agent": "pi (browser)", "originator": "pi"},
            "body": continuation_request,
            "ws_events": [initial_request, continuation_request],
        },
        "response": {
            "status": 101,
            "headers": {},
            "body": {
                "id": "resp_pi_final",
                "status": "completed",
                "instructions": system_prompt,
                "model": "gpt-5.3-codex-spark",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "PI_TOOL_ONE\n/home/liaohch3/src/github.com/liaohch3/claude-tap-3",
                            }
                        ],
                    }
                ],
                "usage": {"input_tokens": 655, "output_tokens": 29, "total_tokens": 684},
            },
            "ws_events": [
                {
                    "type": "response.created",
                    "response": {
                        "id": "resp_pi_tool_call",
                        "status": "in_progress",
                        "instructions": system_prompt,
                        "model": "gpt-5.3-codex-spark",
                        "previous_response_id": None,
                    },
                },
                {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": {
                        "type": "function_call",
                        "name": "bash",
                        "call_id": "call_pi_bash",
                        "arguments": '{"command":"printf \'PI_TOOL_ONE\\n\'; pwd"}',
                    },
                },
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_pi_tool_call",
                        "status": "completed",
                        "instructions": system_prompt,
                        "model": "gpt-5.3-codex-spark",
                        "previous_response_id": None,
                        "output": [
                            {
                                "type": "function_call",
                                "name": "bash",
                                "call_id": "call_pi_bash",
                                "arguments": '{"command":"printf \'PI_TOOL_ONE\\n\'; pwd"}',
                            }
                        ],
                        "usage": {"input_tokens": 530, "output_tokens": 88, "total_tokens": 618},
                    },
                },
                {
                    "type": "response.created",
                    "response": {
                        "id": "resp_pi_final",
                        "status": "in_progress",
                        "instructions": system_prompt,
                        "model": "gpt-5.3-codex-spark",
                        "previous_response_id": "resp_pi_tool_call",
                    },
                },
                {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "PI_TOOL_ONE\n/home/liaohch3/src/github.com/liaohch3/claude-tap-3",
                            }
                        ],
                    },
                },
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_pi_final",
                        "status": "completed",
                        "instructions": system_prompt,
                        "model": "gpt-5.3-codex-spark",
                        "previous_response_id": "resp_pi_tool_call",
                        "output": [
                            {
                                "type": "message",
                                "role": "assistant",
                                "content": [
                                    {
                                        "type": "output_text",
                                        "text": "PI_TOOL_ONE\n/home/liaohch3/src/github.com/liaohch3/claude-tap-3",
                                    }
                                ],
                            }
                        ],
                        "usage": {"input_tokens": 655, "output_tokens": 29, "total_tokens": 684},
                    },
                },
            ],
        },
    }


def _gemini_record() -> dict[str, Any]:
    return {
        "timestamp": "2026-05-13T13:24:00+00:00",
        "request_id": "req_gemini_contract",
        "turn": 1,
        "duration_ms": 100,
        "request": {
            "method": "POST",
            "path": "/v1internal:streamGenerateContent?alt=sse",
            "headers": {"Host": "cloudcode-pa.googleapis.com"},
            "body": {
                "model": "gemini-3-flash-preview",
                "request": {
                    "systemInstruction": {
                        "role": "user",
                        "parts": [{"text": "You are Gemini CLI contract system prompt."}],
                    },
                    "contents": [
                        {"role": "user", "parts": [{"text": "Use shell to inspect the workspace."}]},
                        {
                            "role": "model",
                            "parts": [{"functionCall": {"name": "run_shell_command", "args": {"command": "pwd"}}}],
                        },
                        {
                            "role": "user",
                            "parts": [
                                {
                                    "functionResponse": {
                                        "id": "run_shell_command_1",
                                        "name": "run_shell_command",
                                        "response": {"output": "Output: /repo\nProcess Group PGID: 123"},
                                    }
                                }
                            ],
                        },
                    ],
                    "tools": [
                        {
                            "functionDeclarations": [
                                {
                                    "name": "run_shell_command",
                                    "description": "Runs a shell command.",
                                    "parametersJsonSchema": {
                                        "type": "object",
                                        "properties": {"command": {"type": "string"}},
                                    },
                                }
                            ]
                        }
                    ],
                },
            },
        },
        "response": {
            "status": 200,
            "headers": {},
            "body": (
                _sse_frame(
                    {
                        "response": {
                            "candidates": [
                                {
                                    "content": {
                                        "role": "model",
                                        "parts": [
                                            {"thought": True, "text": "I should run the command."},
                                            {"functionCall": {"name": "run_shell_command", "args": {"command": "pwd"}}},
                                        ],
                                    }
                                }
                            ],
                            "usageMetadata": {
                                "promptTokenCount": 160,
                                "candidatesTokenCount": 5,
                                "cachedContentTokenCount": 80,
                            },
                        }
                    }
                )
                + _sse_frame(
                    {
                        "response": {
                            "candidates": [{"content": {"role": "model", "parts": [{"text": "Gemini final OK."}]}}],
                            "usageMetadata": {
                                "promptTokenCount": 170,
                                "candidatesTokenCount": 13,
                                "cachedContentTokenCount": 80,
                            },
                        }
                    }
                )
            ),
        },
    }


def _bedrock_converse_record() -> dict[str, Any]:
    return {
        "timestamp": "2026-05-13T13:30:00+00:00",
        "request_id": "req_bedrock_converse_contract",
        "turn": 1,
        "duration_ms": 100,
        "request": {
            "method": "POST",
            "path": "/model/anthropic.claude-sonnet-4-20250514-v1:0/converse",
            "headers": {},
            "body": {
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": "Use Bedrock Converse to answer."}],
                    }
                ],
                "tools": [
                    {
                        "name": "lookup",
                        "description": "Look up a value.",
                        "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
                    }
                ],
            },
        },
        "response": {
            "status": 200,
            "headers": {},
            "body": {
                "output": {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"text": "Bedrock Converse final OK."},
                            {
                                "reasoningContent": {
                                    "reasoningText": {
                                        "text": "Bedrock reasoning OK.",
                                        "signature": "sig-contract",
                                    }
                                }
                            },
                            {"toolUse": {"toolUseId": "tool-1", "name": "lookup", "input": {"query": "bedrock"}}},
                        ],
                    }
                },
                "usage": {
                    "inputTokens": 180,
                    "outputTokens": 18,
                    "totalTokens": 198,
                    "cacheReadInputTokens": 60,
                    "cacheWriteInputTokens": 12,
                },
            },
        },
    }


def _contract_cases() -> tuple[ViewerContractCase, ...]:
    return (
        ViewerContractCase(
            name="anthropic_messages",
            records=(_anthropic_messages_record(),),
            expected_sections=("Tools", "System Prompt", "Messages", "Response"),
            expected_system="Claude Code contract system prompt.",
            expected_roles=("user", "assistant", "user"),
            expected_tools=("Read",),
            expected_output_types=("text",),
            expected_usage={"input_tokens": 120, "output_tokens": 9, "cache_read_input_tokens": 40},
            required_detail_text=("Read pyproject.toml.", "project metadata", "Anthropic response OK."),
        ),
        ViewerContractCase(
            name="openai_responses",
            records=(_responses_record(),),
            expected_sections=("Tools", "System Prompt", "Messages", "Response"),
            expected_system="You are Codex contract system prompt.",
            expected_roles=("developer", "user"),
            expected_tools=("exec_command",),
            expected_output_types=("tool_use", "text"),
            expected_usage={"input_tokens": 130, "output_tokens": 14, "cache_read_input_tokens": 50},
            required_detail_text=("Run pwd.", "exec_command", "Responses final OK."),
        ),
        ViewerContractCase(
            name="codex_websocket",
            records=(_codex_websocket_record(),),
            expected_sections=("Tools", "System Prompt", "Request Context", "Response", "SSE Events"),
            expected_system="You are Codex WebSocket contract system prompt.",
            expected_roles=("developer", "user"),
            expected_tools=("exec_command",),
            expected_output_types=("tool_use", "text"),
            expected_usage={"input_tokens": 140, "output_tokens": 8},
            required_detail_text=("Continue after a tool call.", "exec_command", "WebSocket final OK."),
            min_stream_events=3,
        ),
        ViewerContractCase(
            name="codex_reverse_websocket",
            records=(_codex_reverse_websocket_record(),),
            expected_sections=("Tools", "System Prompt", "Request Context", "Response", "SSE Events"),
            expected_system="You are Codex WebSocket contract system prompt.",
            expected_roles=("developer", "user"),
            expected_tools=("exec_command",),
            expected_output_types=("tool_use", "text"),
            expected_usage={"input_tokens": 140, "output_tokens": 8},
            required_detail_text=("Continue after a tool call.", "exec_command", "WebSocket final OK."),
            min_stream_events=3,
        ),
        ViewerContractCase(
            name="chat_completions",
            records=(_chat_completions_record(),),
            expected_sections=("Tools", "System Prompt", "Messages", "Response"),
            expected_system="Kimi contract system prompt.",
            expected_roles=("user", "assistant", "tool"),
            expected_tools=("read_file",),
            expected_output_types=("thinking", "tool_use", "text"),
            expected_usage={"input_tokens": 150, "output_tokens": 10, "cache_read_input_tokens": 70},
            required_detail_text=(
                "Read the project metadata.",
                "read_file",
                "Need to inspect metadata before answering.",
                "inspect_metadata",
                "Chat final OK.",
            ),
        ),
        ViewerContractCase(
            name="opencode_chat_completions",
            records=(_opencode_chat_completions_record(),),
            expected_sections=("Tools", "System Prompt", "Messages", "Response", "SSE Events"),
            expected_system="You are opencode, an interactive CLI tool that helps users with software engineering tasks.",
            expected_roles=("user", "assistant", "tool", "assistant", "user", "assistant", "tool"),
            expected_tools=("bash", "edit"),
            expected_output_types=("thinking", "text"),
            expected_usage={"input_tokens": 14070, "output_tokens": 109, "cache_read_input_tokens": 13952},
            required_detail_text=(
                "OPENCODE_TOOL_ONE",
                "/home/liaohch3/src/github.com/liaohch3/claude-tap-3",
                "OPENCODE_TOOL_TWO",
                "pyproject.toml",
            ),
            min_stream_events=2,
        ),
        ViewerContractCase(
            name="opencode_openai_oauth_responses",
            records=(_opencode_openai_oauth_responses_record(),),
            expected_sections=("Tools", "System Prompt", "Messages", "Response", "SSE Events"),
            expected_system=(
                "You are OpenCode, You and the user share the same workspace "
                "and collaborate to achieve the user's goals."
            ),
            expected_roles=("developer", "user", "assistant", "assistant", "tool"),
            expected_tools=("bash", "read"),
            expected_output_types=("text",),
            expected_usage={"input_tokens": 12424, "output_tokens": 115, "cache_read_input_tokens": 11776},
            required_detail_text=(
                "OPENCODE_OPENAI_OAUTH_TOOL_ONE",
                "/home/liaohch3/src/github.com/liaohch3/claude-tap-3",
                "bash",
            ),
            min_stream_events=1,
        ),
        ViewerContractCase(
            name="pi_openai_oauth_websocket_final_response",
            records=(_pi_openai_oauth_websocket_record(),),
            expected_sections=("Tools", "System Prompt", "Request Context", "Response", "SSE Events"),
            expected_system=(
                "You are an expert coding assistant operating inside pi, a coding agent harness. "
                "You help users by reading files, executing commands, editing code, and writing new files.\n\n"
                "Available tools:\n- bash: Execute bash commands (ls, grep, find, etc.)"
            ),
            expected_roles=("developer", "user", "assistant", "tool"),
            expected_tools=("bash",),
            expected_output_types=("text",),
            expected_usage={"input_tokens": 655, "output_tokens": 29},
            required_detail_text=(
                "PI_TOOL_ONE",
                "/home/liaohch3/src/github.com/liaohch3/claude-tap-3",
                "printf 'PI_TOOL_ONE",
            ),
            min_stream_events=2,
            entry_index=1,
            expected_sidebar_label="Pi",
        ),
        ViewerContractCase(
            name="gemini",
            records=(_gemini_record(),),
            expected_sections=("Tools", "System Prompt", "Messages", "Response", "SSE Events"),
            expected_system="You are Gemini CLI contract system prompt.",
            expected_roles=("user", "assistant", "tool"),
            expected_tools=("run_shell_command",),
            expected_output_types=("thinking", "tool_use", "text"),
            expected_usage={"input_tokens": 170, "output_tokens": 13, "cache_read_input_tokens": 80},
            required_detail_text=("Use shell to inspect the workspace.", "Output: /repo", "Gemini final OK."),
            min_stream_events=2,
        ),
        ViewerContractCase(
            name="bedrock_converse",
            records=(_bedrock_converse_record(),),
            expected_sections=("Tools", "Messages", "Response"),
            expected_system=None,
            expected_roles=("user",),
            expected_tools=("lookup",),
            expected_output_types=("text", "thinking", "tool_use"),
            expected_usage={
                "input_tokens": 180,
                "output_tokens": 18,
                "cache_read_input_tokens": 60,
                "cache_creation_input_tokens": 12,
            },
            required_detail_text=(
                "Use Bedrock Converse to answer.",
                "Bedrock Converse final OK.",
                "Bedrock reasoning OK.",
                "lookup",
            ),
        ),
        ViewerContractCase(
            name="content_block_boundaries",
            records=(_content_block_boundary_record(),),
            expected_sections=("System Prompt", "Messages", "Response"),
            expected_system="System block one.\n\nSystem block two.",
            expected_roles=("developer", "user", "assistant", "tool"),
            expected_tools=(),
            expected_output_types=("text",),
            expected_usage={"input_tokens": 42, "output_tokens": 5},
            required_detail_text=(
                "System block one.",
                "Developer block one.",
                "User block two.",
                "Assistant text block.",
                "Tool result two.",
                "Content block response OK.",
            ),
        ),
        ViewerContractCase(
            name="tool_bloat",
            records=_tool_bloat_records(),
            expected_sections=("Tools", "System Prompt", "Messages", "Response"),
            expected_system="Bloat contract system prompt.",
            expected_roles=("user", "assistant", "user"),
            expected_tools=("grep",),
            expected_output_types=("text",),
            expected_usage={"input_tokens": 200, "output_tokens": 100},
            required_detail_text=("Grep the sources.", "Large tool output", "Analyzed grep result."),
            entry_index=1,
        ),
    )


def _runtime_smoke_records() -> tuple[dict[str, Any], ...]:
    return (
        {
            "request_id": "req_empty_body",
            "turn": 1,
            "request": {"method": "POST", "path": "/v1/messages", "headers": {}, "body": None},
            "response": {"status": 200, "headers": {}, "body": None},
        },
        {
            "request_id": "req_string_bodies",
            "turn": 2,
            "request": {"method": "POST", "path": "/v1/messages", "headers": {}, "body": "not json"},
            "response": {"status": 200, "headers": {}, "body": "plain text response"},
        },
    )


def _sidebar_order_records() -> tuple[dict[str, Any], ...]:
    base = {
        "duration_ms": 100,
        "request": {"method": "POST", "path": "/v1/messages", "headers": {}, "body": {}},
        "response": {"status": 200, "headers": {}, "body": {"content": [{"type": "text", "text": "OK"}]}},
    }
    rows = [
        ("req_order_unknown", 1, "other-model", "First sidebar task"),
        ("req_order_sonnet", 2, "aws.claude-sonnet-4.6", "Second sidebar task"),
        ("req_order_opus", 3, "aws.claude-opus-4.6", "Second sidebar task"),
    ]
    records = []
    for request_id, turn, model, prompt in rows:
        record = json.loads(json.dumps(base))
        record["request_id"] = request_id
        record["turn"] = turn
        record["timestamp"] = f"2026-05-13T13:2{turn}:00+00:00"
        record["request"]["body"]["model"] = model
        record["request"]["body"]["messages"] = [{"role": "user", "content": prompt}]
        records.append(record)
    return tuple(records)


def _claude_code_session_round_records() -> tuple[dict[str, Any], ...]:
    model = "aws.claude-opus-4.6"

    def make_record(
        request_id: str,
        turn: int,
        messages: list[dict[str, Any]],
        response_content: list[dict[str, Any]],
        *,
        system: str | None = None,
        stop_reason: str = "end_turn",
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"model": model, "messages": messages}
        if system is not None:
            body["system"] = system
        return {
            "request_id": request_id,
            "turn": turn,
            "timestamp": f"2026-05-13T13:{20 + turn:02d}:00+00:00",
            "duration_ms": 100,
            "request": {"method": "POST", "path": "/v1/messages", "headers": {}, "body": body},
            "response": {
                "status": 200,
                "headers": {},
                "body": {"stop_reason": stop_reason, "content": response_content},
            },
        }

    first_prompt = "Check configured MCP settings"
    second_prompt = "Diagnose portfolio holdings"
    third_prompt = "Add portfolio positions"
    title_system = 'Generate a concise, sentence-case title for the session. Return JSON with a single "title" field.'
    first_tool = {"type": "tool_use", "id": "tool_1", "name": "ListMcpResourcesTool", "input": {}}
    second_tool = {"type": "tool_use", "id": "tool_2", "name": "portfolio", "input": {}}
    third_tool = {"type": "tool_use", "id": "tool_3", "name": "search_stock_by_name", "input": {"query": "ACME"}}

    return (
        make_record(
            "req_title",
            1,
            [{"role": "user", "content": [{"type": "text", "text": f"<session>\n{first_prompt}\n</session>"}]}],
            [{"type": "text", "text": '{"title": "Check configured MCP settings"}'}],
            system=title_system,
        ),
        make_record(
            "req_first_tool",
            2,
            [{"role": "user", "content": first_prompt}],
            [first_tool],
            stop_reason="tool_use",
        ),
        make_record(
            "req_first_final",
            3,
            [
                {"role": "user", "content": first_prompt},
                {"role": "assistant", "content": [first_tool]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tool_1", "content": "mcp list"}]},
            ],
            [{"type": "text", "text": "Configured MCP server: wyckoff."}],
        ),
        make_record(
            "req_second_tool",
            4,
            [
                {"role": "user", "content": first_prompt},
                {"role": "assistant", "content": "Configured MCP server: wyckoff."},
                {"role": "user", "content": second_prompt},
            ],
            [second_tool],
            stop_reason="tool_use",
        ),
        make_record(
            "req_second_final",
            5,
            [
                {"role": "user", "content": first_prompt},
                {"role": "assistant", "content": "Configured MCP server: wyckoff."},
                {"role": "user", "content": second_prompt},
                {"role": "assistant", "content": [second_tool]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tool_2", "content": "empty"}]},
            ],
            [{"type": "text", "text": "No holdings found."}],
        ),
        make_record(
            "req_third_tool",
            6,
            [
                {"role": "user", "content": first_prompt},
                {"role": "assistant", "content": "Configured MCP server: wyckoff."},
                {"role": "user", "content": second_prompt},
                {"role": "assistant", "content": "No holdings found."},
                {"role": "user", "content": third_prompt},
            ],
            [third_tool],
            stop_reason="tool_use",
        ),
        make_record(
            "req_third_suggestion",
            7,
            [
                {"role": "user", "content": first_prompt},
                {"role": "assistant", "content": "Configured MCP server: wyckoff."},
                {"role": "user", "content": second_prompt},
                {"role": "assistant", "content": "No holdings found."},
                {"role": "user", "content": third_prompt},
                {"role": "assistant", "content": "Portfolio diagnosis complete."},
                {"role": "user", "content": "[SUGGESTION MODE: Suggest what the user might naturally type next.]"},
            ],
            [{"type": "text", "text": "Give me an action plan."}],
        ),
    )


def _codex_app_large_session_records() -> tuple[dict[str, Any], ...]:
    records: list[dict[str, Any]] = []
    session_id = "codex-session-alpha"
    prompts = [
        "Write Codex App runtime wiki",
        "Review live dashboard capture",
        "Fix duplicate Codex App trace rows",
    ]
    for turn in range(1, 61):
        prompt_index = (turn - 1) // 20
        prompt = prompts[prompt_index]
        step = (turn - 1) % 20
        hour = 10 + (turn - 1) // 60
        minute = (turn - 1) % 60
        injected_user_messages = [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "# AGENTS.md instructions\nSkip maintainer automation notes."}
                ],
            },
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "<environment_context>\nskip cwd\n</environment_context>"}],
            },
        ]
        prior_messages: list[dict[str, Any]] = []
        for prior_prompt in prompts[:prompt_index]:
            prior_messages.extend(
                [
                    {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": prior_prompt}],
                    },
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": f"Finished {prior_prompt}."}],
                    },
                ]
            )
        user_message = {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": prompt}],
        }
        continuation_messages: list[dict[str, Any]] = []
        if step:
            continuation_messages = [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": f"Working on {prompt} step {step}."}],
                },
                {
                    "type": "function_call_output",
                    "call_id": f"call-{turn}",
                    "output": f"step {step} output",
                },
            ]
        records.append(
            {
                "timestamp": f"2026-06-13T{hour:02d}:{minute:02d}:00+00:00",
                "request_id": f"req_codexapp_{turn}",
                "turn": turn,
                "duration_ms": 0,
                "transport": "websocket",
                "upstream_base_url": "https://chatgpt.com/backend-api/codex",
                "request": {
                    "method": "WEBSOCKET",
                    "path": "/backend-api/codex/responses",
                    "headers": {"host": "chatgpt.com", "authorization": "Bearer [REDACTED]"},
                    "body": {
                        "type": "response.create",
                        "model": "gpt-5.5",
                        "metadata": {"codex_app_session_id": session_id},
                        "input": [
                            *injected_user_messages,
                            *prior_messages,
                            user_message,
                            *continuation_messages,
                        ],
                    },
                },
                "response": {
                    "status": 101,
                    "headers": {},
                    "body": {
                        "id": f"resp_codexapp_{turn}",
                        "status": "completed",
                        "model": "gpt-5.5",
                        "output": [
                            {
                                "type": "message",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": f"Answer {turn}."}],
                            }
                        ],
                        "usage": {"input_tokens": turn, "output_tokens": 1, "total_tokens": turn + 1},
                    },
                },
            }
        )
    return tuple(records)


def _codex_display_turn_records() -> tuple[dict[str, Any], ...]:
    return (
        {
            "timestamp": "2026-06-01T00:57:34.069390+00:00",
            "request_id": "req_models",
            "turn": 1,
            "duration_ms": 4031,
            "request": {
                "method": "GET",
                "path": "/v1/models?client_version=0.134.0",
                "headers": {},
                "body": None,
            },
            "response": {"status": 200, "headers": {}, "body": {"data": []}},
        },
        {
            "timestamp": "2026-06-01T00:57:40.306340+00:00",
            "request_id": "req_prefetch",
            "turn": 2,
            "duration_ms": 1643,
            "transport": "websocket",
            "request": {
                "method": "WEBSOCKET",
                "path": "/v1/responses",
                "headers": {},
                "body": {"type": "response.create", "model": "gpt-5.5", "generate": False, "input": []},
            },
            "response": {
                "status": 101,
                "headers": {},
                "body": {
                    "id": "resp_prefetch",
                    "model": "gpt-5.5",
                    "generate": False,
                    "output": [],
                    "usage": {"input_tokens": 10088, "output_tokens": 0},
                },
            },
        },
        {
            "timestamp": "2026-06-01T00:58:53.101027+00:00",
            "request_id": "req_first_visible",
            "turn": "2.2",
            "duration_ms": 74200,
            "transport": "websocket",
            "request": {
                "method": "WEBSOCKET",
                "path": "/v1/responses",
                "headers": {},
                "body": {
                    "type": "response.create",
                    "model": "gpt-5.5",
                    "input": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "Clean local branches."}],
                        }
                    ],
                },
            },
            "response": {
                "status": 101,
                "headers": {},
                "body": {
                    "id": "resp_first_visible",
                    "model": "gpt-5.5",
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "I will inspect the worktree."}],
                        }
                    ],
                    "usage": {"input_tokens": 23768, "output_tokens": 407},
                },
            },
        },
        {
            "timestamp": "2026-06-01T00:59:02.416139+00:00",
            "request_id": "req_second_visible",
            "turn": "2.3",
            "duration_ms": 83000,
            "transport": "websocket",
            "request": {
                "method": "WEBSOCKET",
                "path": "/v1/responses",
                "headers": {},
                "body": {
                    "type": "response.create",
                    "model": "gpt-5.5",
                    "previous_response_id": "resp_first_visible",
                    "input": [{"type": "function_call_output", "call_id": "call_status", "output": "{}"}],
                },
            },
            "response": {
                "status": 101,
                "headers": {},
                "body": {
                    "id": "resp_second_visible",
                    "model": "gpt-5.5",
                    "previous_response_id": "resp_first_visible",
                    "output": [
                        {
                            "type": "function_call",
                            "name": "exec_command",
                            "call_id": "call_branch",
                            "arguments": '{"cmd":"git branch"}',
                        }
                    ],
                    "usage": {"input_tokens": 25076, "output_tokens": 303},
                },
            },
        },
        {
            "timestamp": "2026-06-01T00:59:12.416139+00:00",
            "request_id": "req_zero_output_visible",
            "turn": "2.4",
            "duration_ms": 5000,
            "transport": "websocket",
            "request": {
                "method": "WEBSOCKET",
                "path": "/v1/responses",
                "headers": {},
                "body": {
                    "type": "response.create",
                    "model": "simple",
                    "previous_response_id": "resp_second_visible",
                    "input": [{"type": "function_call_output", "call_id": "call_branch", "output": "{}"}],
                },
            },
            "response": {
                "status": 500,
                "headers": {},
                "body": {
                    "error": {"message": "upstream timeout"},
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            },
        },
    )


def _codex_lazy_display_turn_records() -> tuple[dict[str, Any], ...]:
    records = list(_codex_display_turn_records())
    for idx in range(60):
        records.append(
            {
                "timestamp": f"2026-06-01T01:{idx % 60:02d}:00.000000+00:00",
                "request_id": f"req_model_{idx}",
                "turn": 100 + idx,
                "duration_ms": 10,
                "request": {
                    "method": "GET",
                    "path": "/v1/models",
                    "headers": {},
                    "body": None,
                },
                "response": {"status": 200, "headers": {}, "body": {"data": []}},
            }
        )
    return tuple(records)


def _codex_direct_generate_false_records() -> tuple[dict[str, Any], ...]:
    visible = json.loads(json.dumps(_responses_record()))
    visible["request_id"] = "req_direct_visible"
    visible["turn"] = 2
    visible["request"]["body"]["model"] = "gpt-5.5"
    visible["response"]["body"]["id"] = "resp_direct_visible"
    visible["response"]["body"]["usage"] = {"input_tokens": 12, "output_tokens": 1}

    return (
        {
            "timestamp": "2026-06-01T02:00:00.000000+00:00",
            "request_id": "req_direct_prefetch",
            "turn": 1,
            "duration_ms": 20,
            "request": {
                "method": "POST",
                "path": "/v1/responses",
                "headers": {},
                "body": {"model": "gpt-5.5", "generate": False, "input": []},
            },
            "response": {
                "status": 200,
                "headers": {},
                "body": {
                    "id": "resp_direct_prefetch",
                    "model": "gpt-5.5",
                    "generate": False,
                    "output": [],
                    "usage": {"input_tokens": 9, "output_tokens": 0},
                },
            },
        },
        visible,
    )


def _codex_lazy_event_generate_false_records() -> tuple[dict[str, Any], ...]:
    records: list[dict[str, Any]] = [
        {
            "timestamp": "2026-06-01T02:10:00.000000+00:00",
            "request_id": "req_event_prefetch",
            "turn": 1,
            "duration_ms": 20,
            "transport": "websocket",
            "request": {
                "method": "WEBSOCKET",
                "path": "/v1/responses",
                "headers": {},
                "body": {"type": "response.create", "model": "gpt-5.5", "input": []},
                "ws_events": [
                    {
                        "type": "response.create",
                        "data": {"model": "gpt-5.5", "generate": False, "input": []},
                    }
                ],
            },
            "response": {
                "status": 101,
                "headers": {},
                "body": None,
                "ws_events": [
                    {
                        "type": "response.created",
                        "data": {
                            "response": {
                                "id": "resp_event_prefetch",
                                "model": "gpt-5.5",
                                "generate": False,
                            }
                        },
                    },
                    {
                        "type": "response.completed",
                        "data": {
                            "response": {
                                "id": "resp_event_prefetch",
                                "model": "gpt-5.5",
                                "generate": False,
                                "output": [],
                                "usage": {"input_tokens": 11, "output_tokens": 0},
                            }
                        },
                    },
                ],
            },
        },
        {
            "timestamp": "2026-06-01T02:10:01.000000+00:00",
            "request_id": "req_event_visible",
            "turn": 2,
            "duration_ms": 50,
            "transport": "websocket",
            "request": {
                "method": "WEBSOCKET",
                "path": "/v1/responses",
                "headers": {},
                "body": {"type": "response.create", "model": "gpt-5.5", "input": []},
            },
            "response": {
                "status": 101,
                "headers": {},
                "body": None,
                "ws_events": [
                    {
                        "type": "response.created",
                        "data": {"response": {"id": "resp_event_visible", "model": "gpt-5.5"}},
                    },
                    {
                        "type": "response.output_item.done",
                        "data": {
                            "output_index": 0,
                            "item": {
                                "type": "message",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": "event visible"}],
                            },
                        },
                    },
                    {
                        "type": "response.completed",
                        "data": {
                            "response": {
                                "id": "resp_event_visible",
                                "model": "gpt-5.5",
                                "output": [
                                    {
                                        "type": "message",
                                        "role": "assistant",
                                        "content": [{"type": "output_text", "text": "event visible"}],
                                    }
                                ],
                                "usage": {"input_tokens": 12, "output_tokens": 1},
                            }
                        },
                    },
                ],
            },
        },
    ]
    for idx in range(60):
        records.append(
            {
                "timestamp": f"2026-06-01T02:11:{idx % 60:02d}.000000+00:00",
                "request_id": f"req_event_model_{idx}",
                "turn": 100 + idx,
                "duration_ms": 10,
                "request": {
                    "method": "GET",
                    "path": "/v1/models",
                    "headers": {},
                    "body": None,
                },
                "response": {"status": 200, "headers": {}, "body": {"data": []}},
            }
        )
    return tuple(records)


def _write_trace(trace_path: Path, records: tuple[dict[str, Any], ...]) -> None:
    trace_path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def _generate_case_html(tmp_path: Path, name: str, records: tuple[dict[str, Any], ...]) -> Path:
    trace_path = tmp_path / f"{name}.jsonl"
    html_path = tmp_path / f"{name}.html"
    _write_trace(trace_path, records)
    _generate_html_viewer(trace_path, html_path)
    return html_path


def _compact_contract_records() -> tuple[dict[str, Any], ...]:
    records = []
    for turn in (1, 2):
        record = json.loads(json.dumps(_responses_record()))
        record["request_id"] = f"req_compact_contract_{turn}"
        record["turn"] = turn
        record["request"]["body"]["instructions"] = "Shared compact system prompt. " * 120
        record["request"]["body"]["tools"] = [
            {
                "type": "function",
                "name": "exec_command",
                "description": "Large repeated tool schema. " * 120,
                "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}},
            }
        ]
        record["request"]["body"]["input"] = [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": f"Compact bundle turn {turn}."}],
            }
        ]
        record["response"]["body"]["output"] = [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": f"Compact response {turn}."}],
            }
        ]
        records.append(record)
    return tuple(records)


def _open_viewer_with_error_capture(page: Page, html_path: Path) -> list[str]:
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))
    page.on("console", lambda msg: errors.append(f"console.error: {msg.text}") if msg.type == "error" else None)
    page.goto(html_path.resolve().as_uri(), timeout=10000)
    page.wait_for_selector(".sidebar-item", timeout=5000)
    return errors


@pytest.fixture(scope="module")
def chromium_browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.mark.parametrize("case", _contract_cases(), ids=lambda case: case.name)
def test_viewer_semantic_contracts_across_supported_trace_shapes(
    tmp_path: Path, chromium_browser, case: ViewerContractCase
) -> None:
    html_path = _generate_case_html(tmp_path, case.name, case.records)

    page = chromium_browser.new_page()
    try:
        errors = _open_viewer_with_error_capture(page, html_path)
        page.locator(".sidebar-item").nth(case.entry_index).click()
        page.wait_for_selector("#detail .section", timeout=5000)

        result = page.evaluate(
            """(entryIndex) => {
              const entry = entries[entryIndex];
              const body = entry.request.body;
              const output = getResponseOutput(entry);
              const usage = getUsage(entry);
              return {
                sectionTitles: Array.from(document.querySelectorAll('#detail .section .title')).map(el => el.textContent),
                sidebarLabel: document.querySelectorAll('.sidebar-item .si-task')[entryIndex]?.textContent || '',
                system: extractSystem(body) || '',
                roles: getMessages(body).map(message => message.role),
                tools: getRequestTools(body).map(toolDisplayName),
                outputTypes: (output?.content || []).map(block => block.type),
                usage,
                eventCount: getResponseEvents(entry).length,
                detailText: document.querySelector('#detail').innerText,
              };
            }""",
            case.entry_index,
        )
    finally:
        page.close()

    assert errors == []
    assert "Full JSON" in result["sectionTitles"]
    assert any(title != "Full JSON" for title in result["sectionTitles"])
    for section in case.expected_sections:
        assert section in result["sectionTitles"]
    if case.expected_system is not None:
        assert result["system"] == case.expected_system
    if case.expected_sidebar_label is not None:
        assert result["sidebarLabel"] == case.expected_sidebar_label
    assert result["roles"] == list(case.expected_roles)
    assert result["tools"] == list(case.expected_tools)
    assert result["outputTypes"] == list(case.expected_output_types)
    for key, value in case.expected_usage.items():
        assert result["usage"][key] == value
    assert result["eventCount"] >= case.min_stream_events
    for text in case.required_detail_text:
        assert text in result["detailText"]


def test_viewer_detail_tabs_keep_default_view_and_expose_trace_mode(tmp_path: Path, chromium_browser) -> None:
    html_path = _generate_case_html(tmp_path, "detail_tabs", (_responses_record(),))

    page = chromium_browser.new_page()
    try:
        errors = _open_viewer_with_error_capture(page, html_path)
        page.locator(".sidebar-item").first.click()
        page.wait_for_selector('#detail .detail-tab[data-tab="default"].active', timeout=5000)

        default_state = page.evaluate(
            """() => ({
              tabs: Array.from(document.querySelectorAll('#detail .detail-tab')).map(el => ({
                mode: el.dataset.tab,
                label: el.querySelector('span:not(.tab-count)')?.textContent || '',
                active: el.classList.contains('active'),
              })),
              sectionTitles: Array.from(document.querySelectorAll('#detail .section .title')).map(el => el.textContent),
              text: document.querySelector('#detail')?.innerText || '',
            })"""
        )

        page.locator('#detail .detail-tab[data-tab="trace"]').click()
        page.wait_for_selector('#detail .detail-tab[data-tab="trace"].active', timeout=5000)
        trace_state = page.evaluate(
            """() => ({
              sectionCount: document.querySelectorAll('#detail .section').length,
              blockTitles: Array.from(document.querySelectorAll('#detail .trace-block-title .trace-title')).map(el => el.textContent),
              copyButtons: Array.from(document.querySelectorAll('#detail .trace-copy-btn')).map(el => el.textContent),
              formats: Array.from(document.querySelectorAll('#detail .trace-format-btn')).map(el => ({
                format: el.dataset.format,
                label: el.textContent,
                active: el.classList.contains('active'),
              })),
              text: document.querySelector('#detail')?.innerText || '',
            })"""
        )
        page.evaluate(
            """() => {
              window.__copiedTraceText = '';
              window.copyToClipboard = (text, btn) => {
                window.__copiedTraceText = text;
                if (btn) btn.textContent = t('copied');
                return Promise.resolve();
              };
            }"""
        )
        page.locator("#detail .trace-copy-btn").first.click()
        page.wait_for_function("window.__copiedTraceText.includes('Run pwd.')")
        copied_trace_text = page.evaluate("window.__copiedTraceText")

        page.locator('#detail .trace-format-btn[data-format="yaml"]').click()
        page.wait_for_selector('#detail .trace-format-btn[data-format="yaml"].active', timeout=5000)
        yaml_state = page.evaluate(
            """() => ({
              text: document.querySelector('#detail')?.innerText || '',
              codeFormat: document.querySelector('#detail .trace-code')?.dataset.format || '',
              prettyCount: document.querySelectorAll('#detail .trace-pretty').length,
            })"""
        )

        page.locator('#detail .trace-format-btn[data-format="pretty"]').click()
        page.wait_for_selector('#detail .trace-format-btn[data-format="pretty"].active', timeout=5000)
        pretty_state = page.evaluate(
            """() => ({
              text: document.querySelector('#detail')?.innerText || '',
              codeCount: document.querySelectorAll('#detail .trace-code').length,
              prettyCount: document.querySelectorAll('#detail .trace-pretty').length,
            })"""
        )

        remaining_tabs = page.evaluate(
            "() => Array.from(document.querySelectorAll('#detail .detail-tab')).map(el => el.dataset.tab)"
        )
    finally:
        page.close()

    assert errors == []
    assert default_state["tabs"] == [
        {"mode": "default", "label": "Default", "active": True},
        {"mode": "trace", "label": "Trace", "active": False},
    ]
    assert default_state["sectionTitles"] == ["Tools", "System Prompt", "Messages", "Response", "Full JSON"]
    assert "Responses final OK." in default_state["text"]
    assert "Diff with Prev" in default_state["text"]
    assert trace_state["sectionCount"] == 0
    assert trace_state["blockTitles"] == ["Input", "Output", "Metadata"]
    assert trace_state["copyButtons"] == ["Copy", "Copy", "Copy"]
    assert '"messages"' in copied_trace_text
    assert "Run pwd." in copied_trace_text
    assert trace_state["formats"] == [
        {"format": "json", "label": "JSON", "active": True},
        {"format": "yaml", "label": "YAML", "active": False},
        {"format": "pretty", "label": "Pretty", "active": False},
    ]
    assert '"messages"' in trace_state["text"]
    assert "req_responses_contract" in trace_state["text"]
    assert "Responses final OK." in trace_state["text"]
    assert yaml_state["codeFormat"] == "yaml"
    assert yaml_state["prettyCount"] == 0
    assert "messages:" in yaml_state["text"]
    assert "req_responses_contract" in yaml_state["text"]
    assert pretty_state["codeCount"] == 0
    assert pretty_state["prettyCount"] == 3
    assert "messages" in pretty_state["text"]
    assert "req_responses_contract" in pretty_state["text"]
    assert remaining_tabs == ["default", "trace"]


def test_viewer_curl_uses_recorded_upstream_and_historical_host_fallback(tmp_path: Path, chromium_browser) -> None:
    current_record = json.loads(json.dumps(_responses_record()))
    current_record["upstream_base_url"] = "https://gateway.example.test:8443"
    current_record["request"]["path"] = "/custom/v1/responses"
    current_record["request"]["headers"] = {
        "Host": "ignored.example.test",
        "Authorization": "Bearer sk-test...",
    }

    historical_record = json.loads(json.dumps(_responses_record()))
    historical_record["request_id"] = "req_historical_curl"
    historical_record["turn"] = 2
    historical_record["request"]["path"] = "/legacy/v1/responses"
    historical_record["request"]["headers"] = {
        "host": "legacy.example.test:9443",
        "Authorization": "Bearer sk-test...",
    }

    reverse_record = json.loads(json.dumps(_responses_record()))
    reverse_record["request_id"] = "req_historical_reverse_curl"
    reverse_record["turn"] = 3
    reverse_record["request"]["path"] = "/v1/responses"
    reverse_record["request"]["headers"] = {"Host": "127.0.0.1:8080"}

    html_path = _generate_case_html(tmp_path, "curl_upstream", (current_record, historical_record, reverse_record))
    page = chromium_browser.new_page()
    try:
        errors = _open_viewer_with_error_capture(page, html_path)
        page.evaluate(
            """() => {
              window.__copiedCurl = [];
              window.copyToClipboard = (text) => { window.__copiedCurl.push(text); return Promise.resolve(); };
            }"""
        )
        curl_button = '#detail .act-btn[onclick="copyCurl(this)"]'
        page.evaluate("filtered = entries.slice()")
        assert page.evaluate("filtered.length") == 3
        for index in range(3):
            page.evaluate("index => { activeIdx = index; renderDetail(filtered[index]); }", index)
            page.locator(curl_button).click()
        copied = page.evaluate("window.__copiedCurl")
    finally:
        page.close()

    assert errors == []
    assert copied[0].startswith("curl -X POST 'https://gateway.example.test:8443/custom/v1/responses'")
    assert "ignored.example.test" not in copied[0]
    assert copied[1].startswith("curl -X POST 'https://legacy.example.test:9443/legacy/v1/responses'")
    assert "Authorization: Bearer sk-test..." in copied[0]
    assert "Authorization: Bearer sk-test..." in copied[1]
    assert copied[2].startswith("curl -X POST 'https://api.anthropic.com/v1/responses'")
    assert "127.0.0.1:8080" not in copied[2]
    assert "sk-test-secret" not in "\n".join(copied)


def test_viewer_tool_call_params_can_expand_escaped_string_newlines(tmp_path: Path, chromium_browser) -> None:
    record = json.loads(json.dumps(_responses_record()))
    decoded_command = "python - <<'PY'\nprint(\"hello\")\nPY"
    record["response"]["body"]["output"][0]["arguments"] = json.dumps(
        {"cmd": decoded_command, "yield_time_ms": 1000},
        ensure_ascii=False,
    )
    html_path = _generate_case_html(tmp_path, "tool_call_param_escapes", (record,))

    page = chromium_browser.new_page()
    try:
        errors = _open_viewer_with_error_capture(page, html_path)
        page.locator(".sidebar-item").first.click()
        page.wait_for_selector("#detail .section", timeout=5000)
        page.wait_for_selector("#detail .tool-input-toggle", timeout=5000)
        page.evaluate(
            """() => {
              window.__copiedToolInput = '';
              window.copyToClipboard = (text) => {
                window.__copiedToolInput = text;
                return Promise.resolve();
              };
            }"""
        )

        input_view = page.locator("#detail .tool-input-view").first
        raw_text = input_view.inner_text()
        input_view_count = page.locator("#detail .tool-input-view").count()
        toggle_text_before = page.locator("#detail .tool-input-toggle").first.inner_text()
        copy_button_count = page.locator("#detail .tool-input-copy").count()
        page.locator("#detail .tool-input-copy").first.click()
        copied_raw_text = page.evaluate("window.__copiedToolInput")
        page.locator("#detail .tool-input-toggle").first.click()
        page.wait_for_selector("#detail .tool-input-view.expanded", timeout=5000)
        decoded_text = input_view.inner_text()
        toggle_text_expanded = page.locator("#detail .tool-input-toggle").first.inner_text()
        page.locator("#detail .tool-input-copy").first.click()
        copied_decoded_text = page.evaluate("window.__copiedToolInput")
        expanded_view_count = page.locator("#detail .tool-input-view").count()
        decoded_box_count = page.locator("#detail .tool-input-decoded").count()
        decoded_copy_button_count = page.locator("#detail .tool-input-copy-decoded").count()
        page.locator("#detail .tool-input-toggle").first.click()
        restored_text = input_view.inner_text()
        full_json_buttons = page.locator("#detail .json-view .tool-input-toggle").count()
        full_json_copy_buttons = page.locator("#detail .json-view .tool-input-copy").count()
    finally:
        page.close()

    assert errors == []
    assert "\\nprint" in raw_text
    assert "python - <<'PY'\nprint(\"hello\")\nPY" in decoded_text
    assert copied_raw_text == raw_text
    assert copied_decoded_text == decoded_text
    assert toggle_text_before == "\u21b5"
    assert toggle_text_expanded == "Raw"
    assert restored_text == raw_text
    assert input_view_count == expanded_view_count == 1
    assert decoded_box_count == 0
    assert copy_button_count == 1
    assert decoded_copy_button_count == 0
    assert full_json_buttons == 0
    assert full_json_copy_buttons == 0


def test_viewer_renders_embedded_and_dropped_compact_trace_bundle(tmp_path: Path, chromium_browser) -> None:
    records = _compact_contract_records()
    bundle = build_compact_trace_bundle(list(records))
    html_path = tmp_path / "compact.html"
    bundle_path = tmp_path / "compact.ctap.json"
    blank_html_path = tmp_path / "blank.html"

    _generate_html_viewer_from_compact_bundle(
        bundle,
        html_path,
        display_trace_path=bundle_path,
        display_html_path=html_path,
    )
    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    blank_html_path.write_text(_read_viewer_template(), encoding="utf-8")

    page = chromium_browser.new_page()
    try:
        errors = _open_viewer_with_error_capture(page, html_path)
        page.locator(".sidebar-item").nth(1).click()
        page.wait_for_selector("#detail .section", timeout=5000)
        embedded_state = page.evaluate(
            """() => ({
              entryCount: entries.length,
              hasBlobRef: JSON.stringify(EMBEDDED_TRACE_COMPACT_DATA).includes('__claude_tap_blob_ref__'),
              detailText: document.querySelector('#detail')?.innerText || '',
            })"""
        )
    finally:
        page.close()

    assert errors == []
    assert embedded_state["entryCount"] == 2
    assert embedded_state["hasBlobRef"] is True
    assert "Compact bundle turn 2." in embedded_state["detailText"]
    assert "Compact response 2." in embedded_state["detailText"]

    drop_page = chromium_browser.new_page()
    drop_errors: list[str] = []
    drop_page.on("pageerror", lambda exc: drop_errors.append(f"pageerror: {exc}"))
    drop_page.on(
        "console", lambda msg: drop_errors.append(f"console.error: {msg.text}") if msg.type == "error" else None
    )
    try:
        drop_page.goto(blank_html_path.resolve().as_uri(), timeout=10000)
        drop_page.set_input_files("#file-input", str(bundle_path))
        drop_page.wait_for_selector(".sidebar-item", timeout=5000)
        dropped_state = drop_page.evaluate(
            """() => ({
              entryCount: entries.length,
              sidebarText: document.querySelector('#sidebar')?.innerText || '',
              detailText: document.querySelector('#detail')?.innerText || '',
            })"""
        )
    finally:
        drop_page.close()

    assert drop_errors == []
    assert dropped_state["entryCount"] == 2
    assert "Compact bundle turn 1." in dropped_state["detailText"]
    assert "gpt-5.4" in dropped_state["sidebarText"]


def test_viewer_does_not_synthesize_messages_for_empty_responses_input(tmp_path: Path, chromium_browser) -> None:
    html_path = _generate_case_html(tmp_path, "responses_empty_input", (_responses_empty_input_record(),))

    page = chromium_browser.new_page()
    try:
        errors = _open_viewer_with_error_capture(page, html_path)
        page.locator(".sidebar-item").first.click()
        page.wait_for_selector("#detail .section", timeout=5000)
        state = page.evaluate(
            """() => ({
              roles: getMessages(entries[0].request.body).map(message => message.role),
              sectionTitles: Array.from(document.querySelectorAll('#detail .section .title')).map(el => el.textContent),
              detailText: document.querySelector('#detail')?.innerText || '',
            })"""
        )
    finally:
        page.close()

    assert errors == []
    assert state["roles"] == []
    assert "System Prompt" in state["sectionTitles"]
    assert "Tools" in state["sectionTitles"]
    assert "Messages" not in state["sectionTitles"]
    assert "You are Codex contract system prompt." in state["detailText"]


def test_viewer_sidebar_order_can_switch_between_model_turn_and_session_sequence(
    tmp_path: Path, chromium_browser
) -> None:
    html_path = _generate_case_html(tmp_path, "sidebar_order", _sidebar_order_records())

    page = chromium_browser.new_page()
    page.add_init_script("localStorage.setItem('claude-tap-sidebar-order', 'model')")
    try:
        errors = _open_viewer_with_error_capture(page, html_path)
        model_state = page.evaluate(
            """() => ({
              label: document.querySelector('#sidebar-sort-label')?.textContent || '',
              buttons: Array.from(document.querySelectorAll('.sidebar-sort-btn')).map(el => ({
                mode: el.dataset.sortMode,
                label: el.textContent,
                active: el.classList.contains('active'),
              })),
              groups: Array.from(document.querySelectorAll('.sidebar-group-header .group-name')).map(el => el.textContent),
              turns: Array.from(document.querySelectorAll('.sidebar-item .si-turn')).map(el => el.textContent),
            })"""
        )

        page.locator('.sidebar-sort-btn[data-sort-mode="turn"]').click()
        page.wait_for_selector('.sidebar-sort-btn[data-sort-mode="turn"].active', timeout=5000)
        turn_state = page.evaluate(
            """() => ({
              buttons: Array.from(document.querySelectorAll('.sidebar-sort-btn')).map(el => ({
                mode: el.dataset.sortMode,
                label: el.textContent,
                active: el.classList.contains('active'),
              })),
              groupCount: document.querySelectorAll('.sidebar-group-header').length,
              turns: Array.from(document.querySelectorAll('.sidebar-item .si-turn')).map(el => el.textContent),
            })"""
        )

        page.locator('.sidebar-sort-btn[data-sort-mode="session"]').click()
        page.wait_for_selector('.sidebar-sort-btn[data-sort-mode="session"].active', timeout=5000)
        session_state = page.evaluate(
            """() => ({
              buttons: Array.from(document.querySelectorAll('.sidebar-sort-btn')).map(el => ({
                mode: el.dataset.sortMode,
                label: el.textContent,
                active: el.classList.contains('active'),
              })),
              groups: Array.from(document.querySelectorAll('.sidebar-group-header .group-name')).map(el => el.textContent),
              counts: Array.from(document.querySelectorAll('.sidebar-group-header .group-count')).map(el => el.textContent),
              turns: Array.from(document.querySelectorAll('.sidebar-item .si-turn')).map(el => el.textContent),
            })"""
        )
    finally:
        page.close()

    assert errors == []
    assert model_state["label"] == "Order"
    assert model_state["buttons"] == [
        {"mode": "model", "label": "Model", "active": True},
        {"mode": "turn", "label": "Turn", "active": False},
        {"mode": "session", "label": "Query", "active": False},
    ]
    assert model_state["groups"] == ["aws.claude-opus-4.6", "aws.claude-sonnet-4.6", "other-model"]
    assert model_state["turns"] == ["Turn 3", "Turn 2", "Turn 1"]
    assert turn_state["buttons"] == [
        {"mode": "model", "label": "Model", "active": False},
        {"mode": "turn", "label": "Turn", "active": True},
        {"mode": "session", "label": "Query", "active": False},
    ]
    assert turn_state["groupCount"] == 0
    assert turn_state["turns"] == ["Turn 1", "Turn 2", "Turn 3"]
    assert session_state["buttons"] == [
        {"mode": "model", "label": "Model", "active": False},
        {"mode": "turn", "label": "Turn", "active": False},
        {"mode": "session", "label": "Query", "active": True},
    ]
    assert session_state["groups"] == [
        "Query 1 - First sidebar task",
        "Query 2 - Second sidebar task",
        "Query 3 - Second sidebar task",
    ]
    assert session_state["counts"] == ["1", "1", "1"]
    assert session_state["turns"] == ["Turn 1", "Turn 2", "Turn 3"]


def test_viewer_session_order_groups_claude_code_tool_loop_rounds(tmp_path: Path, chromium_browser) -> None:
    html_path = _generate_case_html(tmp_path, "claude_code_session_rounds", _claude_code_session_round_records())

    page = chromium_browser.new_page()
    page.add_init_script("localStorage.setItem('claude-tap-sidebar-order', 'session')")
    try:
        errors = _open_viewer_with_error_capture(page, html_path)
        state = page.evaluate(
            """() => ({
              groups: Array.from(document.querySelectorAll('.sidebar-group-header .group-name')).map(el => el.textContent),
              counts: Array.from(document.querySelectorAll('.sidebar-group-header .group-count')).map(el => el.textContent),
              turns: Array.from(document.querySelectorAll('.sidebar-item .si-turn')).map(el => el.textContent),
              headerText: document.querySelector('#sidebar')?.innerText || '',
            })"""
        )
    finally:
        page.close()

    assert errors == []
    assert state["groups"] == [
        "Query 1 - Check configured MCP settings",
        "Query 2 - Diagnose portfolio holdings",
        "Query 3 - Add portfolio positions",
    ]
    assert state["counts"] == ["3", "2", "2"]
    assert state["turns"] == ["Turn 1", "Turn 2", "Turn 3", "Turn 4", "Turn 5", "Turn 6", "Turn 7"]
    assert "SUGGESTION MODE" not in state["headerText"]


def test_viewer_session_order_groups_large_codex_app_sessions_in_virtual_mode(tmp_path: Path, chromium_browser) -> None:
    html_path = _generate_case_html(tmp_path, "codex_app_large_sessions", _codex_app_large_session_records())

    page = chromium_browser.new_page()
    page.add_init_script("localStorage.setItem('claude-tap-sidebar-order', 'session')")
    try:
        errors = _open_viewer_with_error_capture(page, html_path)
        state = page.evaluate(
            """() => ({
              virtualMode,
              groups: Array.from(document.querySelectorAll('.sidebar-group-header .group-name')).map(el => el.textContent),
              counts: Array.from(document.querySelectorAll('.sidebar-group-header .group-count')).map(el => el.textContent),
              virtualGroups: vsFilteredItems
                .filter(row => row.type === 'group')
                .map(row => row.group.userText),
              virtualCounts: vsFilteredItems
                .filter(row => row.type === 'group')
                .map(row => String(row.group.items.length)),
              rowCount: vsFilteredItems.length,
              visualCount: visualOrder.length,
            })"""
        )
    finally:
        page.close()

    assert errors == []
    assert state["virtualMode"] is True
    assert state["groups"] == ["Query 1 - Write Codex App runtime wiki"]
    assert state["counts"] == ["20"]
    assert state["virtualGroups"] == [
        "Write Codex App runtime wiki",
        "Review live dashboard capture",
        "Fix duplicate Codex App trace rows",
    ]
    assert state["virtualCounts"] == ["20", "20", "20"]
    assert state["rowCount"] == 63
    assert state["visualCount"] == 60


def test_viewer_codex_display_turns_skip_capture_control_records(tmp_path: Path, chromium_browser) -> None:
    html_path = _generate_case_html(tmp_path, "codex_display_turns", _codex_display_turn_records())

    page = chromium_browser.new_page()
    try:
        errors = _open_viewer_with_error_capture(page, html_path)
        state = page.evaluate(
            """() => ({
              sidebarTurns: Array.from(document.querySelectorAll('.sidebar-item .si-turn')).map(el => el.textContent),
              codexEntries: entries
                .filter(entry => entry.request?.path === '/v1/responses')
                .map(entry => ({
                  turn: entry.turn,
                  displayTurn: entry.display_turn,
                  captureTurn: entry.capture_turn,
                  requestId: entry.request_id,
                })),
              resetTurns: (() => {
                const extra = JSON.parse(JSON.stringify(entries.find(entry => entry.request?.path === '/v1/responses' && entry.display_turn === 3)));
                delete extra.display_turn;
                extra.turn = '2.5';
                extra.capture_turn = '2.5';
                extra.request_id = 'req_after_reset';
                return normalizeDisplayTurns([...entries, extra], true)
                  .filter(entry => entry.request?.path === '/v1/responses' && entry.display_turn !== undefined)
                  .map(entry => entry.display_turn);
              })(),
            })"""
        )
    finally:
        page.close()

    assert errors == []
    assert state["sidebarTurns"] == ["Turn 1", "Turn 2", "Turn 3"]
    assert state["codexEntries"] == [
        {
            "turn": "2.2",
            "displayTurn": 1,
            "captureTurn": "2.2",
            "requestId": "req_first_visible",
        },
        {
            "turn": "2.3",
            "displayTurn": 2,
            "captureTurn": "2.3",
            "requestId": "req_second_visible",
        },
        {
            "turn": "2.4",
            "displayTurn": 3,
            "captureTurn": "2.4",
            "requestId": "req_zero_output_visible",
        },
    ]
    assert state["resetTurns"] == [1, 2, 3, 4]


def test_viewer_direct_responses_generate_false_is_not_navigable(tmp_path: Path, chromium_browser) -> None:
    html_path = _generate_case_html(
        tmp_path,
        "codex_direct_generate_false",
        _codex_direct_generate_false_records(),
    )

    page = chromium_browser.new_page()
    try:
        errors = _open_viewer_with_error_capture(page, html_path)
        state = page.evaluate(
            """() => ({
              sidebarTurns: Array.from(document.querySelectorAll('.sidebar-item .si-turn')).map(el => el.textContent),
              filteredRequestIds: filtered.map(entry => entry.request_id),
              entriesById: Object.fromEntries(entries.map(entry => [entry.request_id, {
                displayTurn: entry.display_turn ?? null,
                captureTurn: entry.capture_turn ?? null,
                navigable: isNavigableTraceEntry(entry),
              }])),
            })"""
        )
    finally:
        page.close()

    assert errors == []
    assert state["sidebarTurns"] == ["Turn 1"]
    assert state["filteredRequestIds"] == ["req_direct_visible"]
    assert state["entriesById"]["req_direct_prefetch"] == {
        "displayTurn": None,
        "captureTurn": 1,
        "navigable": False,
    }
    assert state["entriesById"]["req_direct_visible"] == {
        "displayTurn": 1,
        "captureTurn": 2,
        "navigable": True,
    }


def test_viewer_codex_lazy_display_turns_skip_capture_control_records(tmp_path: Path, chromium_browser) -> None:
    html_path = _generate_case_html(tmp_path, "codex_lazy_display_turns", _codex_lazy_display_turn_records())

    page = chromium_browser.new_page()
    try:
        errors = _open_viewer_with_error_capture(page, html_path)
        state = page.evaluate(
            """() => ({
              usesCompactBundle: typeof EMBEDDED_TRACE_COMPACT_DATA !== 'undefined',
              usesMetadataMode: typeof EMBEDDED_TRACE_META !== 'undefined',
              sidebarTurns: Array.from(document.querySelectorAll('.sidebar-item .si-turn')).map(el => el.textContent),
              sidebarPaths: Array.from(document.querySelectorAll('.sidebar-item .si-path')).map(el => el.textContent),
              filteredRequestIds: filtered.map(entry => entry.request_id),
              codexEntries: entries
                .filter(entry => entry.request?.path === '/v1/responses')
                .map(entry => ({
                  requestId: entry.request_id,
                  turn: entry.turn,
                  displayTurn: entry.display_turn ?? null,
                  captureTurn: entry.capture_turn ?? null,
                  responseOutputCount: entry.response?.body?.output?.length ?? 0,
                  outputTokens: entry.response?.body?.usage?.output_tokens ?? 0,
                  navigable: isNavigableTraceEntry(entry),
                })),
            })"""
        )
    finally:
        page.close()

    assert errors == []
    assert state["usesCompactBundle"] is True
    assert state["usesMetadataMode"] is False
    assert state["sidebarTurns"] == ["Turn 1", "Turn 2", "Turn 3"]
    assert state["sidebarPaths"] == ["WEBSOCKET /v1/responses", "WEBSOCKET /v1/responses", "WEBSOCKET /v1/responses"]
    assert state["filteredRequestIds"] == ["req_first_visible", "req_second_visible", "req_zero_output_visible"]
    assert state["codexEntries"] == [
        {
            "requestId": "req_first_visible",
            "turn": "2.2",
            "displayTurn": 1,
            "captureTurn": "2.2",
            "responseOutputCount": 1,
            "outputTokens": 407,
            "navigable": True,
        },
        {
            "requestId": "req_second_visible",
            "turn": "2.3",
            "displayTurn": 2,
            "captureTurn": "2.3",
            "responseOutputCount": 1,
            "outputTokens": 303,
            "navigable": True,
        },
        {
            "requestId": "req_zero_output_visible",
            "turn": "2.4",
            "displayTurn": 3,
            "captureTurn": "2.4",
            "responseOutputCount": 0,
            "outputTokens": 0,
            "navigable": True,
        },
    ]


def test_viewer_codex_lazy_stubs_read_generate_from_websocket_events(tmp_path: Path, chromium_browser) -> None:
    html_path = _generate_case_html(
        tmp_path,
        "codex_lazy_event_generate_false",
        _codex_lazy_event_generate_false_records(),
    )

    page = chromium_browser.new_page()
    try:
        errors = _open_viewer_with_error_capture(page, html_path)
        state = page.evaluate(
            """() => ({
              usesCompactBundle: typeof EMBEDDED_TRACE_COMPACT_DATA !== 'undefined',
              usesMetadataMode: typeof EMBEDDED_TRACE_META !== 'undefined',
              sidebarTurns: Array.from(document.querySelectorAll('.sidebar-item .si-turn')).map(el => el.textContent),
              filteredRequestIds: filtered.map(entry => entry.request_id),
              eventEntries: entries
                .filter(entry => entry.request_id.startsWith('req_event_') && entry.request?.path === '/v1/responses')
                .map(entry => ({
                  requestId: entry.request_id,
                  displayTurn: entry.display_turn ?? null,
                  captureTurn: entry.capture_turn ?? null,
                  requestGenerate: entry.request?.body?.generate ?? null,
                  responseGenerate: entry.response?.body?.generate ?? null,
                  responseOutputCount: entry.response?.body?.output?.length ?? 0,
                  outputTokens: entry.response?.body?.usage?.output_tokens ?? 0,
                  navigable: isNavigableTraceEntry(entry),
                })),
            })"""
        )
    finally:
        page.close()

    assert errors == []
    assert state["usesCompactBundle"] is True
    assert state["usesMetadataMode"] is False
    assert state["sidebarTurns"] == ["Turn 1"]
    assert state["filteredRequestIds"] == ["req_event_visible"]
    assert state["eventEntries"] == [
        {
            "requestId": "req_event_visible",
            "displayTurn": 1,
            "captureTurn": 2,
            "requestGenerate": None,
            "responseGenerate": None,
            "responseOutputCount": 1,
            "outputTokens": 1,
            "navigable": True,
        },
    ]


def test_viewer_codex_lazy_trace_tab_preserves_display_turn_metadata(tmp_path: Path, chromium_browser) -> None:
    html_path = _generate_case_html(
        tmp_path,
        "codex_lazy_trace_display_turns",
        _codex_lazy_display_turn_records(),
    )

    page = chromium_browser.new_page()
    try:
        errors = _open_viewer_with_error_capture(page, html_path)
        state = page.evaluate(
            """() => {
              function metadataText() {
                const blocks = Array.from(document.querySelectorAll('#detail .trace-block'));
                const block = blocks.find(el => el.querySelector('.trace-title')?.textContent === 'Metadata');
                return block?.innerText || '';
              }
              const idx = filtered.findIndex(entry => entry.request?.path === '/v1/responses' && entry.display_turn === 1);
              selectEntry(idx);
              document.querySelector('#detail .detail-tab[data-tab="trace"]').click();
              const jsonMetadata = metadataText();
              document.querySelector('#detail .trace-format-btn[data-format="yaml"]').click();
              const yamlMetadata = metadataText();
              document.querySelector('#detail .trace-format-btn[data-format="pretty"]').click();
              const prettyMetadata = metadataText();
              return {
                selectedIdx: idx,
                activeDisplayTurn: filtered[activeIdx]?.display_turn ?? null,
                jsonMetadata,
                yamlMetadata,
                prettyMetadata,
              };
            }"""
        )
    finally:
        page.close()

    assert errors == []
    assert state["selectedIdx"] >= 0
    assert state["activeDisplayTurn"] == 1
    assert '"display_turn": 1' in state["jsonMetadata"]
    assert '"capture_turn": "2.2"' in state["jsonMetadata"]
    assert "display_turn: 1" in state["yamlMetadata"]
    assert "capture_turn: 2.2" in state["yamlMetadata"]
    assert "display_turn" in state["prettyMetadata"]
    assert "capture_turn" in state["prettyMetadata"]


def test_viewer_session_group_hover_shows_full_truncated_user_input(tmp_path: Path, chromium_browser) -> None:
    long_prompt = (
        "Investigate why the dashboard session group title is truncated, then preserve this full original "
        "user input in a hover tooltip so maintainers can read the complete request without opening the turn."
    )
    record = {
        "request_id": "req_long_session_prompt",
        "turn": 1,
        "timestamp": "2026-05-13T13:21:00+00:00",
        "duration_ms": 100,
        "request": {
            "method": "POST",
            "path": "/v1/messages",
            "headers": {},
            "body": {
                "model": "aws.claude-sonnet-4.6",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "<system-reminder>\nskip injected context\n</system-reminder>"},
                            {
                                "type": "text",
                                "text": "<local-command-caveat>\nskip local command context\n</local-command-caveat>",
                            },
                            {"type": "text", "text": long_prompt},
                            {"type": "text", "text": "[Image: source: /tmp/screenshot.png]"},
                        ],
                    }
                ],
            },
        },
        "response": {
            "status": 200,
            "headers": {},
            "body": {"content": [{"type": "text", "text": "OK"}]},
        },
    }
    injected_continuation = json.loads(json.dumps(record))
    injected_continuation["request_id"] = "req_injected_context_only"
    injected_continuation["turn"] = 2
    injected_continuation["request"]["body"]["messages"] = [
        {"role": "user", "content": "<system-reminder>\nThe following skills are available...\n</system-reminder>"}
    ]
    json_title_record = json.loads(json.dumps(record))
    json_title_record["request_id"] = "req_json_title"
    json_title_record["turn"] = 3
    json_title_record["request"]["body"]["messages"] = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": '{"title":"Fix login button on mobile"}'},
                {"type": "text", "text": "<system-reminder>\nInjected context\n</system-reminder>"},
            ],
        }
    ]
    html_path = _generate_case_html(
        tmp_path,
        "session_hover_tooltip",
        (record, injected_continuation, json_title_record),
    )

    page = chromium_browser.new_page()
    page.add_init_script("localStorage.setItem('claude-tap-sidebar-order', 'session')")
    try:
        errors = _open_viewer_with_error_capture(page, html_path)
        header = page.locator(".sidebar-group-header").nth(0)
        assert header.locator(".group-name").inner_text().endswith("...")
        assert "<system-reminder>" not in header.locator(".group-name").inner_text()
        assert header.locator(".group-count").inner_text() == "2"
        json_title_header = page.locator(".sidebar-group-header").nth(1)
        json_title_name = json_title_header.locator(".group-name").inner_text()
        assert "fix login button on mobile" in json_title_name.lower()
        assert "{" not in json_title_name

        header.hover()
        page.wait_for_selector(".session-hover-tooltip.visible", timeout=5000)
        tooltip_text = page.locator(".session-hover-tooltip.visible").inner_text()
    finally:
        page.close()

    assert errors == []
    assert tooltip_text == long_prompt


def test_viewer_recovers_session_title_image_placeholders(tmp_path: Path, chromium_browser) -> None:
    prompt = "My local TNTCloud nodes all time out, but the same account works on another computer."
    image_data = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
    title_record = {
        "timestamp": "2026-05-13T13:21:00+00:00",
        "request_id": "req_title_image_placeholder",
        "turn": 1,
        "duration_ms": 100,
        "request": {
            "method": "POST",
            "path": "/v1/messages",
            "headers": {},
            "body": {
                "model": "aws.claude-sonnet-4.6",
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": f"<session>\n[Image #1] {prompt}\n</session>"}],
                    }
                ],
            },
        },
        "response": {
            "status": 200,
            "headers": {},
            "body": {"content": [{"type": "text", "text": '{"title":"TNTCloud timeout"}'}]},
        },
    }
    actual_record = json.loads(json.dumps(title_record))
    actual_record["request_id"] = "req_actual_image"
    actual_record["turn"] = 2
    actual_record["request"]["body"]["messages"] = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": f"[Image #1] {prompt}"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image_data}},
            ],
        }
    ]

    html_path = _generate_case_html(tmp_path, "image-placeholder-recovery", (title_record, actual_record))
    page = chromium_browser.new_page()
    try:
        errors = _open_viewer_with_error_capture(page, html_path)
        page.locator(".sidebar-item").nth(0).click()
        page.wait_for_selector("#detail .msg.user img.message-image", timeout=5000)
        result = page.evaluate(
            """() => ({
              imageCount: document.querySelectorAll('#detail .msg.user img.message-image').length,
              imageSrc: document.querySelector('#detail .msg.user img.message-image')?.getAttribute('src') || '',
              text: document.querySelector('#detail .msg.user')?.textContent || ''
            })"""
        )
    finally:
        page.close()

    assert errors == []
    assert result["imageCount"] == 1
    assert result["imageSrc"].startswith("data:image/png;base64,")
    assert "[Image #1]" in result["text"]


def test_viewer_runtime_smoke_handles_degenerate_records_without_js_errors(tmp_path: Path, chromium_browser) -> None:
    html_path = _generate_case_html(tmp_path, "runtime_smoke", _runtime_smoke_records())

    page = chromium_browser.new_page()
    try:
        errors = _open_viewer_with_error_capture(page, html_path)
        sidebar_count = page.locator(".sidebar-item").count()
        for index in range(sidebar_count):
            page.locator(".sidebar-item").nth(index).click()
            page.wait_for_selector("#detail .section", timeout=5000)
            assert "Full JSON" in page.locator("#detail").inner_text()
    finally:
        page.close()

    assert errors == []
    assert sidebar_count == len(_runtime_smoke_records())


def test_viewer_empty_embedded_trace_renders_explicit_no_api_calls_state(tmp_path: Path, chromium_browser) -> None:
    html_path = _generate_case_html(tmp_path, "empty_trace", ())

    page = chromium_browser.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))
    page.on("console", lambda msg: errors.append(f"console.error: {msg.text}") if msg.type == "error" else None)
    try:
        page.goto(html_path.resolve().as_uri(), timeout=10000)
        page.wait_for_selector(".empty-trace-state", timeout=5000)
        state = page.evaluate(
            """() => ({
              title: document.querySelector('#empty-trace-title')?.textContent || '',
              description: document.querySelector('#empty-trace-desc')?.textContent || '',
              count: document.querySelector('#empty-trace-count')?.textContent || '',
              hint: document.querySelector('#empty-trace-hint')?.textContent || '',
              sidebarItemCount: document.querySelectorAll('.sidebar-item').length,
              sidebarDisplay: getComputedStyle(document.querySelector('#sidebar-wrap')).display,
              detailDisplay: getComputedStyle(document.querySelector('#detail')).display,
              pathText: document.querySelector('#trace-path-bar')?.innerText || '',
              oldDropTitlePresent: Boolean(document.querySelector('#drop-title')),
              fileInputPresent: Boolean(document.querySelector('#file-input')),
            })"""
        )
    finally:
        page.close()

    assert errors == []
    assert state["title"] == "No API calls captured"
    assert "generated" in state["description"]
    assert state["count"] == "Captured API calls: 0"
    assert "real empty run" in state["hint"]
    assert state["sidebarItemCount"] == 0
    assert state["sidebarDisplay"] == "none"
    assert state["detailDisplay"] == "none"
    assert "empty_trace.jsonl" in state["pathText"]
    assert "empty_trace.html" in state["pathText"]
    assert state["oldDropTitlePresent"] is False
    assert state["fileInputPresent"] is True


def test_viewer_trace_path_copy_handles_apostrophes(tmp_path: Path, chromium_browser) -> None:
    html_path = _generate_case_html(tmp_path, "user's_trace", (_anthropic_messages_record(),))

    page = chromium_browser.new_page()
    try:
        errors = _open_viewer_with_error_capture(page, html_path)
        copy_button = page.locator("#trace-path-bar .tp-copy").first
        assert copy_button.get_attribute("onclick") is None
        assert "user's_trace.jsonl" in (copy_button.get_attribute("data-copy-path") or "")
    finally:
        page.close()

    assert errors == []


def test_viewer_iframe_embed_query_hides_chrome_and_keeps_trace_loaded(tmp_path: Path, chromium_browser) -> None:
    html_path = _generate_case_html(tmp_path, "embed_trace", (_anthropic_messages_record(),))
    query = "embed=1&hideHeader=1&hidePath=1&hideHistory=1&hideControls=1&density=compact&theme=light"

    page = chromium_browser.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))
    page.on("console", lambda msg: errors.append(f"console.error: {msg.text}") if msg.type == "error" else None)
    try:
        page.goto(
            f"{html_path.resolve().as_uri()}?{query}",
            timeout=10000,
        )
        page.wait_for_selector(".sidebar-item", timeout=5000)
        state = page.evaluate(
            """() => ({
              embedMode: document.documentElement.dataset.embedMode || '',
              bodyClasses: Array.from(document.body.classList),
              headerDisplay: getComputedStyle(document.querySelector('.header')).display,
              pathDisplay: getComputedStyle(document.querySelector('#trace-path-bar')).display,
              themeToggleDisplay: getComputedStyle(document.querySelector('#theme-toggle')).display,
              langSelectDisplay: getComputedStyle(document.querySelector('#lang-select')).display,
              searchBarDisplay: getComputedStyle(document.querySelector('#search-bar')).display,
              sidebarSortDisplay: getComputedStyle(document.querySelector('#sidebar-sort')).display,
              detailTabsDisplay: getComputedStyle(document.querySelector('.detail-inspector-bar')).display,
              actionBarDisplay: getComputedStyle(document.querySelector('.action-bar')).display,
              sidebarWidth: Math.round(document.querySelector('#sidebar-wrap').getBoundingClientRect().width),
              theme: document.documentElement.dataset.theme || 'light',
              sidebarItemCount: document.querySelectorAll('.sidebar-item').length,
            })"""
        )
    finally:
        page.close()

    assert errors == []
    assert state["embedMode"] == "true"
    assert "embed-mode" in state["bodyClasses"]
    assert "embed-compact" in state["bodyClasses"]
    assert state["headerDisplay"] == "none"
    assert state["pathDisplay"] == "none"
    assert state["themeToggleDisplay"] == "none"
    assert state["langSelectDisplay"] == "none"
    assert state["searchBarDisplay"] == "none"
    assert state["sidebarSortDisplay"] == "none"
    assert state["detailTabsDisplay"] == "none"
    assert state["actionBarDisplay"] == "none"
    assert state["sidebarWidth"] <= 280
    assert state["theme"] == "light"
    assert state["sidebarItemCount"] == 1

    sandbox_page = chromium_browser.new_page()
    sandbox_errors: list[str] = []
    sandbox_page.on("pageerror", lambda exc: sandbox_errors.append(f"pageerror: {exc}"))
    sandbox_page.on(
        "console",
        lambda msg: sandbox_errors.append(f"console.error: {msg.text}") if msg.type == "error" else None,
    )
    harness_path = tmp_path / "embed_harness.html"
    harness_path.write_text(
        f'<iframe sandbox="allow-scripts" src="{html_path.resolve().as_uri()}?{query}" '
        'style="width:1200px;height:800px"></iframe>',
        encoding="utf-8",
    )
    try:
        sandbox_page.goto(harness_path.resolve().as_uri(), timeout=10000)
        frame = sandbox_page.frame_locator("iframe")
        frame.locator(".sidebar-item").first.wait_for(state="attached", timeout=5000)
        sandbox_state = frame.locator("body").evaluate(
            """() => ({
              embedMode: document.documentElement.dataset.embedMode || '',
              headerDisplay: getComputedStyle(document.querySelector('.header')).display,
              pathDisplay: getComputedStyle(document.querySelector('#trace-path-bar')).display,
              sidebarItemCount: document.querySelectorAll('.sidebar-item').length,
              dropZoneDisplay: getComputedStyle(document.querySelector('#drop-zone')).display,
            })"""
        )
    finally:
        sandbox_page.close()

    assert sandbox_errors == []
    assert sandbox_state["embedMode"] == "true"
    assert sandbox_state["headerDisplay"] == "none"
    assert sandbox_state["pathDisplay"] == "none"
    assert sandbox_state["sidebarItemCount"] == 1
    assert sandbox_state["dropZoneDisplay"] == "none"


def test_viewer_v8_coverage_exercises_core_inline_js_functions(tmp_path: Path, chromium_browser) -> None:
    records = tuple(record for case in _contract_cases() for record in case.records)
    html_path = _generate_case_html(tmp_path, "v8_coverage", records)
    required_functions = {
        "renderDetail",
        "extractSystem",
        "getMessages",
        "getRequestTools",
        "getDetailTools",
        "cursorTranscriptObservedTools",
        "cursorTranscriptConversationKey",
        "jsonSchemaTypeFromValue",
        "mergeObservedToolUse",
        "getUsage",
        "getResponseEvents",
        "getResponseOutput",
        "copyCurl",
        "geminiMessages",
        "geminiResponseOutput",
        "renderTools",
        "renderImageBlock",
        "sessionTurnDiscriminator",
        "showSessionTooltip",
    }

    page = chromium_browser.new_page()
    page.add_init_script(
        "window.__TRACE_SESSION_EXPORTS__ = {compact: 'coverage.json', log: 'coverage.log', html: 'coverage.html'};"
    )
    try:
        session = page.context.new_cdp_session(page)
        session.send("Profiler.enable")
        session.send("Profiler.startPreciseCoverage", {"callCount": True, "detailed": True})
        errors = _open_viewer_with_error_capture(page, html_path)

        export_items = page.locator("#viewer-actions .export-menu-item")
        assert export_items.count() == 2
        assert export_items.all_text_contents() == ["Export JSON", "Export HTML"]
        assert page.locator('#viewer-actions a[href="coverage.log"]').count() == 0

        entry_count = page.evaluate("entries.length")
        for index in range(entry_count):
            page.evaluate("entryIndex => renderDetail(entries[entryIndex])", index)
            page.wait_for_selector("#detail .section", timeout=5000)
            page.evaluate(
                """(entryIndex) => {
                  const entry = entries[entryIndex];
                  const body = entry.request.body;
                  getMessages(body);
                  getRequestTools(body);
                  getDetailTools(entry, body, getResponsePayload(entry));
                  jsonSchemaTypeFromValue(null);
                  jsonSchemaTypeFromValue(1);
                  jsonSchemaTypeFromValue(1.5);
                  jsonSchemaTypeFromValue({ nested: true });
                  mergeObservedToolUse(new Map(), {
                    type: 'tool_use',
                    name: 'Glob',
                    input: { glob_pattern: '**/*.py' },
                  });
                  cursorTranscriptConversationKey({ capture: { cursor_transcript_id: 'coverage' } });
                  cursorTranscriptConversationKey({ request: { path: '/cursor/transcript/coverage/turn/1/step/1' } });
                  cursorTranscriptConversationKey({});
                  cursorTranscriptObservedTools({
                    transport: 'cursor-transcript',
                    request: { path: '/cursor/transcript/coverage/turn/1/step/1' },
                    response: { body: { content: [{ type: 'tool_use', name: 'Read', input: { path: 'a.py' } }] } },
                  });
                  extractSystem(body);
                  getUsage(entry);
                  getResponseEvents(entry);
                  getResponseOutput(entry);
                  const jsonSection = Array.from(document.querySelectorAll('#detail .section'))
                    .find(el => el.querySelector('.title')?.textContent === t('section_json'));
                  const jsonToggle = jsonSection?.querySelector('.jt-toggle');
                  if (jsonToggle) {
                    jsonToggle.click();
                    jsonToggle.click();
                  }
                }""",
                index,
            )

        page.evaluate(
            """() => {
              const imageBlock = {
                type: 'image',
                source: {
                  type: 'base64',
                  media_type: 'image/png',
                  data: 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII='
                }
              };
              imageLookupKey('<session>[Image #1] coverage prompt</session>');
              isInlineImageUrl('data:image/png;base64,abc');
              imageSourceFromBlock(imageBlock);
              imageBlocksForContent([{ type: 'text', text: '[Image #1] coverage prompt' }, imageBlock]);
              imageSourceKey(imageBlock);
              buildSessionImageRegistry();
              naturalTextFromPromptPayload({ prompt: 'coverage prompt' });
              if (entries.length) {
                sessionTurnDiscriminator(entries[0]);
                sessionKeyForEntry(entries[0], null);
                matchSearch(entries[0], '1');
                const originalPrompt = window.prompt;
                window.prompt = () => '1';
                promptJumpToTurn();
                window.prompt = originalPrompt;
                _buildDiffTargetOptions(Math.min(1, filtered.length - 1));
                if (filtered.length > 1) showDiffForIdx(1, null, 0);
              }
              const stubEntry = buildStubEntry({
                turn: '2.2',
                transport: 'websocket',
                method: 'WEBSOCKET',
                path: '/v1/responses',
                model: 'gpt-5.5',
                request_generate: true,
                response_output_count: 1,
                output_tokens: 1,
              }, 0);
              normalizeDisplayTurns([stubEntry], true);
              renderImageElement('data:image/png;base64,abc', 'coverage image');
              renderImageElementForBlock(imageBlock);
              document.body.insertAdjacentHTML('beforeend', renderImageBlock(imageBlock, 0, 1, { frameBlocks: true }));
              renderViewerActions();
              valueHasReadableEscapes({ cmd: 'printf "coverage\\\\n"' });
              decodeEscapedTextForView('line1\\\\nline2\\\\t\\\\u4e00');
              document.body.insertAdjacentHTML(
                'beforeend',
                renderToolInput({ cmd: 'printf "coverage\\n"', yield_time_ms: 1000 })
              );
              window.copyToClipboard = () => Promise.resolve();
              copyCurl(null);
              document.querySelector('.tool-input-toggle')?.click();
              const tooltipTrigger = document.querySelector('.sidebar-group-header') || document.createElement('div');
              if (!tooltipTrigger.isConnected) document.body.appendChild(tooltipTrigger);
              tooltipTrigger.dataset.fullUserInput = 'coverage tooltip prompt';
              sessionTooltip();
              showSessionTooltip(tooltipTrigger);
              hideSessionTooltip(tooltipTrigger);
            }"""
        )

        coverage = session.send("Profiler.takePreciseCoverage")
        session.send("Profiler.stopPreciseCoverage")
        session.send("Profiler.disable")
    finally:
        page.close()

    covered_names: set[str] = set()
    used_main_script_bytes = 0
    for script in coverage["result"]:
        if not script.get("url", "").endswith("v8_coverage.html"):
            continue
        functions = script.get("functions", [])
        if len(functions) < 50:
            continue
        for function in functions:
            ranges = function.get("ranges", [])
            if any(item.get("count", 0) > 0 for item in ranges):
                name = function.get("functionName")
                if name:
                    covered_names.add(name)
                used_main_script_bytes += sum(
                    item.get("endOffset", 0) - item.get("startOffset", 0) for item in ranges if item.get("count", 0) > 0
                )

    assert errors == []
    assert required_functions <= covered_names
    assert used_main_script_bytes > 50_000


def test_viewer_visual_layout_contracts_cover_css_modes(tmp_path: Path, chromium_browser) -> None:
    records = tuple(record for case in _contract_cases() for record in case.records)
    html_path = _generate_case_html(tmp_path, "visual_contract", records)

    page = chromium_browser.new_page(viewport={"width": 1440, "height": 1000})
    try:
        errors = _open_viewer_with_error_capture(page, html_path)
        page.evaluate(
            "requestId => renderDetail(entries.find(entry => entry.request_id === requestId))", "req_gemini_contract"
        )
        page.evaluate(
            """() => {
              const toolsSection = Array.from(document.querySelectorAll('#detail .section'))
                .find(section => section.querySelector('.title')?.textContent === 'Tools');
              if (toolsSection && !toolsSection.querySelector('.section-body')?.classList.contains('open')) {
                toolsSection.querySelector('.section-header').click();
              }
            }"""
        )

        def snapshot(width: int, height: int, theme: str, mobile: bool = False) -> dict:
            page.set_viewport_size({"width": width, "height": height})
            page.evaluate("theme => document.documentElement.setAttribute('data-theme', theme)", theme)
            if mobile:
                page.evaluate("mobileShowDetail()")
            return page.evaluate(
                """() => {
                  const rect = selector => {
                    const el = document.querySelector(selector);
                    if (!el) return null;
                    const r = el.getBoundingClientRect();
                    return { width: r.width, height: r.height, left: r.left, right: r.right, top: r.top, bottom: r.bottom };
                  };
                  const color = selector => getComputedStyle(document.querySelector(selector)).backgroundColor;
                  return {
                    overflowX: document.documentElement.scrollWidth - window.innerWidth,
                    bodyBg: getComputedStyle(document.body).backgroundColor,
                    userMsgBg: color('.msg.user'),
                    sidebar: rect('#sidebar-wrap'),
                    detail: rect('#detail'),
                    sectionHeader: rect('.section-header'),
                    tokenBar: rect('.token-bar'),
                    message: rect('.msg'),
                    toolBlock: rect('.tool-block'),
                    response: rect('.section-body.open .content-block'),
                    sectionCount: document.querySelectorAll('#detail .section').length,
                  };
                }"""
            )

        desktop_light = snapshot(1440, 1000, "light")
        desktop_dark = snapshot(1440, 1000, "dark")
        page.evaluate(
            "requestId => renderDetail(entries.find(entry => entry.request_id === requestId))",
            "req_content_block_boundary_contract",
        )
        dark_content_block = page.evaluate(
            """() => {
              document.documentElement.setAttribute('data-theme', 'dark');
              const block = document.querySelector('.content-block.block-framed');
              if (!block) return null;
              const style = getComputedStyle(block);
              return {
                backgroundColor: style.backgroundColor,
                borderLeftWidth: style.borderLeftWidth,
              };
            }"""
        )
        page.evaluate(
            "requestId => renderDetail(entries.find(entry => entry.request_id === requestId))", "req_gemini_contract"
        )
        mobile_dark = snapshot(390, 900, "dark", mobile=True)
    finally:
        page.close()

    assert errors == []
    for result in (desktop_light, desktop_dark, mobile_dark):
        assert result["overflowX"] <= 2
        assert result["sectionCount"] >= 5
        assert result["sectionHeader"]["height"] >= 28
        assert result["tokenBar"]["width"] > 250
        assert result["message"]["height"] > 40
        assert result["toolBlock"]["width"] > 250
        assert result["response"]["height"] > 20

    assert desktop_light["sidebar"]["width"] >= 280
    assert desktop_light["detail"]["width"] >= 1000
    assert desktop_dark["sidebar"]["width"] >= 280
    assert desktop_dark["detail"]["width"] >= 1000
    assert desktop_light["bodyBg"] != desktop_dark["bodyBg"]
    assert desktop_light["userMsgBg"] != desktop_dark["userMsgBg"]
    assert dark_content_block is not None
    assert dark_content_block["backgroundColor"] != "rgba(0, 0, 0, 0)"
    assert dark_content_block["borderLeftWidth"] == "1px"

    assert mobile_dark["sidebar"]["width"] == 0
    assert mobile_dark["detail"]["width"] == 390
    assert mobile_dark["detail"]["left"] == 0


def test_viewer_session_identical_prompts_image_tags_and_early_title_generation(
    tmp_path: Path, chromium_browser
) -> None:
    model = "aws.claude-opus-4.6"
    title_system = 'Generate a concise, sentence-case title for the session. Return JSON with a single "title" field.'

    def make_record(
        request_id: str,
        turn: int,
        messages: list[dict[str, Any]],
        response_content: list[dict[str, Any]],
        *,
        system: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"model": model, "messages": messages}
        if system is not None:
            body["system"] = system
        return {
            "request_id": request_id,
            "turn": turn,
            "timestamp": f"2026-05-13T13:{20 + turn:02d}:00+00:00",
            "duration_ms": 100,
            "request": {"method": "POST", "path": "/v1/messages", "headers": {}, "body": body},
            "response": {
                "status": 200,
                "headers": {},
                "body": {"stop_reason": "end_turn", "content": response_content},
            },
        }

    records = (
        # Turn 1
        make_record(
            "req_t1",
            1,
            [{"role": "user", "content": "继续"}],
            [{"type": "text", "text": "Turn 1 done"}],
        ),
        # Turn 1 title-gen
        make_record(
            "req_t1_title",
            2,
            [{"role": "user", "content": "继续"}],
            [{"type": "text", "text": '{"title": "Group1"}'}],
            system=title_system,
        ),
        # Turn 2 title-gen arrives before the real request with the same prompt.
        make_record(
            "req_t2_title",
            3,
            [{"role": "user", "content": "继续"}],
            [{"type": "text", "text": '{"title": "Group2"}'}],
            system=title_system,
        ),
        # Turn 2 (identical prompt)
        make_record(
            "req_t2",
            4,
            [{"role": "user", "content": "继续"}],
            [{"type": "text", "text": "Turn 2 done"}],
        ),
        # Turn 3 (image wrappers)
        make_record(
            "req_t3",
            5,
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "<image name=[Image #1]>"},
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=",
                            },
                        },
                        {"type": "text", "text": "</image>"},
                        {"type": "text", "text": "Analyze the flowchart"},
                    ],
                }
            ],
            [{"type": "text", "text": "Turn 3 done"}],
        ),
    )

    html_path = _generate_case_html(tmp_path, "identical_prompts_and_image_tags", records)
    page = chromium_browser.new_page()
    page.add_init_script("localStorage.setItem('claude-tap-sidebar-order', 'session')")
    try:
        errors = _open_viewer_with_error_capture(page, html_path)
        state = page.evaluate(
            """() => ({
              groups: Array.from(document.querySelectorAll('.sidebar-group-header .group-name')).map(el => el.textContent),
              counts: Array.from(document.querySelectorAll('.sidebar-group-header .group-count')).map(el => el.textContent),
            })"""
        )
    finally:
        page.close()

    assert errors == []
    assert state["groups"] == [
        "Query 1 - 继续",
        "Query 2 - 继续",
        "Query 3 - Analyze the flowchart",
    ]
    assert state["counts"] == ["2", "2", "1"]


def test_viewer_codex_global_search_skips_non_navigable_and_orders_by_capture_turn(
    tmp_path: Path, chromium_browser
) -> None:
    records = (
        {
            "timestamp": "2026-06-01T00:57:34.000Z",
            "request_id": "req_models",
            "turn": 1,
            "duration_ms": 100,
            "request": {
                "method": "GET",
                "path": "/v1/models",
                "headers": {},
                "body": None,
            },
            "response": {"status": 200, "headers": {}, "body": {"data": []}},
        },
        {
            "timestamp": "2026-06-01T00:57:40.000Z",
            "request_id": "req_prefetch",
            "turn": 2,
            "duration_ms": 100,
            "transport": "websocket",
            "request": {
                "method": "WEBSOCKET",
                "path": "/v1/responses",
                "headers": {},
                "body": {
                    "type": "response.create",
                    "model": "gpt-hidden-model",
                    "generate": False,
                    "input": [],
                },
            },
            "response": {
                "status": 101,
                "headers": {},
                "body": {
                    "id": "resp_prefetch",
                    "model": "gpt-hidden-model",
                    "generate": False,
                    "output": [],
                    "usage": {"input_tokens": 10, "output_tokens": 0},
                },
            },
        },
        {
            "timestamp": "2026-06-01T00:58:53.000Z",
            "request_id": "req_response_2",
            "turn": 2,
            "duration_ms": 100,
            "transport": "websocket",
            "request": {
                "method": "WEBSOCKET",
                "path": "/v1/responses",
                "headers": {},
                "body": {
                    "type": "response.create",
                    "model": "gpt-5.5",
                    "input": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": "hello",
                        }
                    ],
                },
            },
            "response": {
                "status": 101,
                "headers": {},
                "body": {
                    "id": "resp_response_2",
                    "model": "gpt-5.5",
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": "hi",
                        }
                    ],
                    "usage": {"input_tokens": 10, "output_tokens": 10},
                },
            },
        },
        {
            "timestamp": "2026-06-01T00:59:00.000Z",
            "request_id": "req_mcp_between",
            "turn": 3,
            "duration_ms": 100,
            "request": {
                "method": "POST",
                "path": "/v1/mcp/list",
                "headers": {},
                "body": {"query": "mcp-query"},
            },
            "response": {
                "status": 200,
                "headers": {},
                "body": {"tools": []},
            },
        },
        {
            "timestamp": "2026-06-01T00:59:02.000Z",
            "request_id": "req_response_4",
            "turn": 4,
            "duration_ms": 100,
            "transport": "websocket",
            "request": {
                "method": "WEBSOCKET",
                "path": "/v1/responses",
                "headers": {},
                "body": {
                    "type": "response.create",
                    "model": "gpt-5.5",
                    "previous_response_id": "resp_response_2",
                    "input": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": "then what",
                        }
                    ],
                },
            },
            "response": {
                "status": 101,
                "headers": {},
                "body": {
                    "id": "resp_response_4",
                    "model": "gpt-5.5",
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": "that is it",
                        }
                    ],
                    "usage": {"input_tokens": 10, "output_tokens": 10},
                },
            },
        },
    )

    html_path = _generate_case_html(tmp_path, "codex_search_sort", records)
    page = chromium_browser.new_page()
    try:
        errors = _open_viewer_with_error_capture(page, html_path)
        # Verify global search skips the hidden model
        page.evaluate("() => openGlobalSearch()")
        page.evaluate("() => { $('#global-search-input').value = 'gpt-hidden-model'; recalcGlobalSearchMatches(); }")
        search_state = page.evaluate("() => ({ totalMatches: globalSearchState.totalMatches })")

        # Expand paths and check sorting (should be chronological: turn 2 -> 3 -> 4)
        page.evaluate("() => { closeGlobalSearch(); activePaths.add('/v1/mcp/list'); applyFilter(); }")
        debug_state = page.evaluate(
            """() => ({
              entries: entries.map(e => ({
                request_id: e.request_id,
                turn: e.turn,
                capture_turn: e.capture_turn,
                display_turn: e.display_turn,
                captureTurnValue: captureTurnValue(e),
                displayTurnValue: displayTurnValue(e),
                isNavigable: isNavigableTraceEntry(e),
              })),
              filtered: filtered.map(e => e.request_id),
            })"""
        )
        sorted_ids = [rid for rid in debug_state["filtered"] if rid != "req_models"]
    finally:
        page.close()

    assert errors == []
    assert search_state["totalMatches"] == 0
    assert sorted_ids == ["req_response_2", "req_mcp_between", "req_response_4"]


def test_viewer_tool_bloat_badges_and_banner(tmp_path: Path, chromium_browser) -> None:
    records = _tool_bloat_records()
    html_path = _generate_case_html(tmp_path, "tool_bloat", records)
    page = chromium_browser.new_page()
    try:
        errors = _open_viewer_with_error_capture(page, html_path)

        # Turn 2 carries a 25 KB tool result, so its sidebar row is badged.
        bloat_badge = page.locator(".sidebar-item[data-idx='1'] .si-bloat-badge")
        assert bloat_badge.count() == 1
        assert "KB" in bloat_badge.text_content()

        page.locator(".sidebar-item[data-idx='1']").click()
        page.wait_for_selector("#detail .tool-bloat-alert", timeout=5000)
        assert "Large tool output" in page.locator("#detail .tool-bloat-alert").text_content()

        # Turn 1 has no oversized result, so nothing is flagged in either place.
        page.locator(".sidebar-item[data-idx='0']").click()
        page.wait_for_selector("#detail .tok-item", timeout=5000)
        assert page.locator("#detail .tool-bloat-alert").count() == 0
        assert page.locator(".sidebar-item[data-idx='0'] .si-bloat-badge").count() == 0

        # Turn 3 returns a 25 KB base64 image.  An image is billed by dimension, so
        # neither detector may count its encoding as result text.
        page.locator(".sidebar-item[data-idx='2']").click()
        page.wait_for_selector("#detail .tok-item", timeout=5000)
        assert page.locator("#detail .tool-bloat-alert").count() == 0
        assert page.locator(".sidebar-item[data-idx='2'] .si-bloat-badge").count() == 0

        # Turn 4 is the Responses equivalent: a computer-use screenshot arrives as
        # a `computer_screenshot` output, and normalizing it to an image block is
        # what keeps its base64 out of the byte count.
        page.locator(".sidebar-item[data-idx='3']").click()
        page.wait_for_selector("#detail .tok-item", timeout=5000)
        assert page.locator("#detail .tool-bloat-alert").count() == 0
        assert page.locator(".sidebar-item[data-idx='3'] .si-bloat-badge").count() == 0
        # The screenshot renders as an image rather than a wall of base64 text.
        assert page.locator("#detail img").count() >= 1
    finally:
        page.close()

    assert errors == []


def test_detect_tool_bloat_sizes_by_bytes_and_skips_images() -> None:
    """The Python detector must agree with the JS one on unit and threshold.

    A CJK result short enough to pass a character-count check still crosses the
    byte threshold, and an image payload must never be counted as context text.
    """
    from claude_tap.viewer import TOOL_BLOAT_MIN_BYTES, _detect_tool_bloat

    cjk_chars = TOOL_BLOAT_MIN_BYTES // 3 + 10  # 3 bytes each in UTF-8
    assert cjk_chars < TOOL_BLOAT_MIN_BYTES
    cjk = _detect_tool_bloat([{"role": "user", "content": [{"type": "tool_result", "content": "中" * cjk_chars}]}])
    assert cjk is not None
    assert cjk["byte_count"] >= TOOL_BLOAT_MIN_BYTES

    assert (
        _detect_tool_bloat(
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "content": [{"type": "image", "source": {"type": "base64", "data": "x" * 50000}}],
                        }
                    ],
                }
            ]
        )
        is None
    )

    # A non-string `text` must not crash metadata generation for the trace.
    nonstring = _detect_tool_bloat(
        [
            {
                "role": "user",
                "content": [{"type": "tool_result", "content": [{"type": "text", "text": None}, {"big": "y" * 25000}]}],
            }
        ]
    )
    assert nonstring is not None

    # Below threshold yields nothing at all, rather than a zero-sized record.
    assert _detect_tool_bloat([{"role": "user", "content": [{"type": "tool_result", "content": "short"}]}]) is None

    # OpenAI Chat Completions puts the result straight on a tool-role message.
    assert _detect_tool_bloat([{"role": "tool", "content": "z" * 25000}]) is not None

    # Bedrock Converse camelCase blocks are matched too.
    assert (
        _detect_tool_bloat([{"role": "user", "content": [{"toolResult": {"content": [{"text": "w" * 25000}]}}]}])
        is not None
    )


def test_tool_result_text_flattens_every_content_shape_seen_on_the_wire() -> None:
    """The flattener sits in metadata generation for the whole trace.

    A shape it does not expect must degrade to a size estimate, never raise, so
    the shapes below are the ones traces actually carry: bare strings inside a
    list, a scalar where a block was expected, a dict content instead of a list,
    and a stray None.
    """
    from claude_tap.viewer import TOOL_BLOAT_MIN_BYTES, _detect_tool_bloat, _tool_result_text

    # Anthropic sends a bare string content; a list may also hold bare strings.
    assert _tool_result_text("plain") == "plain"
    assert _tool_result_text(["one", "two"]) == "one\ntwo"

    # A scalar where a block belongs is serialized rather than skipped, so its
    # size still counts toward the turn.  None carries no text at all.
    assert _tool_result_text([42]) == "42"
    assert _tool_result_text([None]) == ""
    assert _tool_result_text(None) == ""

    # A dict content is measured; an image dict is not, on the same reasoning as
    # an image block inside a list.  The separators match JSON.stringify so the
    # two detectors size the same payload identically.
    assert _tool_result_text({"stdout": "ok"}) == '{"stdout":"ok"}'
    assert _tool_result_text({"type": "image", "source": {"data": "x" * 50000}}) == ""
    assert _tool_result_text({"image": {"source": {"data": "x" * 50000}}}) == ""

    # A scalar content is stringified rather than dropped.
    assert _tool_result_text(7) == "7"

    # The shapes above reach the detector intact: a long bare string inside a
    # list, and a dict content, both cross the threshold.
    assert _detect_tool_bloat([{"role": "user", "content": [{"type": "tool_result", "content": ["q" * 25000]}]}])
    dict_content = _detect_tool_bloat(
        [{"role": "user", "content": [{"type": "tool_result", "content": {"stdout": "q" * 25000}}]}]
    )
    assert dict_content is not None
    assert dict_content["byte_count"] >= TOOL_BLOAT_MIN_BYTES

    # A non-dict message and a non-dict block are skipped without raising.
    assert _detect_tool_bloat(["not a message"]) is None
    assert _detect_tool_bloat([{"role": "user", "content": ["not a block"]}]) is None


def test_a_text_field_collapses_the_part_only_for_a_real_text_block() -> None:
    """`text` alone does not stand for the part; `renderContent` needs `type: "text"`.

    A structured result part carrying a string `text` beside other payload fields is
    displayed whole, because the renderer special-cases only `type === "text"` and
    dumps every other part. Collapsing to `text` on the string check alone sized
    `"summary"` for `{"text": "summary", "logs": <25 KB>}` in both detectors, so
    25 KB of context the reader can see on screen produced no badge and no banner.
    A text-ish type is not enough either: an `output_text` part is dumped whole too,
    so whitelisting it would hide the same 25 KB behind a two-byte measurement.
    """
    from claude_tap.viewer import TOOL_BLOAT_MIN_BYTES, _detect_tool_bloat, _tool_result_text

    # The one type renderContent renders as bare text keeps collapsing.
    assert _tool_result_text([{"type": "text", "text": "hello"}]) == "hello"

    # Every other part is measured whole, `text` included, because it is shown whole.
    assert _tool_result_text([{"text": "summary"}]) == '{"text":"summary"}'
    assert _tool_result_text([{"type": "other", "text": "x"}]) == '{"type":"other","text":"x"}'
    assert _tool_result_text([{"type": "input_text", "text": "hi"}]) == '{"type":"input_text","text":"hi"}'
    assert _tool_result_text([{"type": "output_text", "text": "o"}]) == '{"type":"output_text","text":"o"}'

    # So the siblings reach the detector and the payload is flagged.
    structured = _detect_tool_bloat(
        [
            {
                "role": "user",
                "content": [{"type": "tool_result", "content": [{"text": "summary", "logs": "L" * 25000}]}],
            }
        ]
    )
    assert structured is not None
    assert structured["byte_count"] >= TOOL_BLOAT_MIN_BYTES
    # A real text block of the same size is still measured by its text alone.
    assert _tool_result_text([{"type": "text", "text": "s", "logs": "L" * 25000}]) == "s"
    # A text-ish type is not, since the renderer shows those siblings.
    for shown_whole in ("input_text", "output_text"):
        flagged = _detect_tool_bloat(
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "content": [{"type": shown_whole, "text": "ok", "logs": "L" * 25000}],
                        }
                    ],
                }
            ]
        )
        assert flagged is not None, shown_whole
        assert flagged["byte_count"] >= TOOL_BLOAT_MIN_BYTES, shown_whole


def test_a_structured_type_field_is_payload_rather_than_an_image_tag() -> None:
    """A `type` holding a list or dict would make the set test raise.

    `unhashable type` inside the flattener aborts metadata generation for the
    whole trace, while `Set.has` in the browser simply reports false, so the
    unguarded membership test turns one crafted result into a viewer that
    cannot be generated at all.
    """
    from claude_tap.viewer import TOOL_BLOAT_MIN_BYTES, _detect_tool_bloat, _tool_result_text

    for bad_type in ([], {}, 7, None):
        part = {"type": bad_type, "logs": "y" * 25000}
        text = _tool_result_text([part])
        assert len(text.encode("utf-8")) >= TOOL_BLOAT_MIN_BYTES
        assert _detect_tool_bloat([{"role": "user", "content": [{"type": "tool_result", "content": [part]}]}])

    # The nested-image predicate reads the same field and needs the same guard.
    assert _tool_result_text([{"image": {"type": []}, "logs": "y" * 25000}])
    assert _tool_result_text({"type": [], "stdout": "ok"}) == '{"type":[],"stdout":"ok"}'


def test_an_empty_result_part_keeps_its_separator_in_both_detectors() -> None:
    """Python retains an empty part; a truthiness filter in JS would not.

    At the threshold that one separator is the whole difference, so a result
    would earn a sidebar badge whose detail view renders no banner.  Parts that
    are genuinely dropped -- images and nulls -- lose their separator on both
    sides alike.
    """
    from claude_tap.viewer import TOOL_BLOAT_MIN_BYTES, _tool_result_text

    below = "x" * (TOOL_BLOAT_MIN_BYTES - 1)
    assert len(_tool_result_text([below, ""]).encode("utf-8")) == TOOL_BLOAT_MIN_BYTES
    assert len(_tool_result_text([below, {"type": "text", "text": ""}]).encode("utf-8")) == TOOL_BLOAT_MIN_BYTES

    at = "x" * TOOL_BLOAT_MIN_BYTES
    assert len(_tool_result_text([at, {"type": "image", "source": {"data": "zz"}}]).encode("utf-8")) == (
        TOOL_BLOAT_MIN_BYTES
    )
    assert len(_tool_result_text([at, None]).encode("utf-8")) == TOOL_BLOAT_MIN_BYTES


def test_a_call_output_without_output_sizes_the_same_raw_leftovers() -> None:
    """The display path pretty-prints the leftover fields; the scan must not.

    Two spaces of indent per level is enough to move a result across the
    threshold in the detail banner while the sidebar, measuring the compact
    form, stays below it.
    """
    from claude_tap.viewer import _response_tool_result_content, _tool_result_text

    item = {"type": "function_call_output", "call_id": "c1", "status": "completed", "results": ["x"] * 1500}
    payload = _response_tool_result_content(item, for_bloat=True)
    assert payload == {"results": item["results"]}

    display = _response_tool_result_content(item)
    assert isinstance(display, str)
    assert len(_tool_result_text(payload).encode("utf-8")) < len(display.encode("utf-8")) * 3


def test_a_cjk_result_measures_the_same_in_both_detectors() -> None:
    """Escaping non-ASCII would inflate a structured payload roughly sixfold.

    `JSON.stringify` leaves the character as itself, three UTF-8 bytes; Python's
    default escapes it to six ASCII ones.  A payload that only crosses the
    threshold under the escaped measurement gets a sidebar badge whose detail
    view then renders no warning.
    """
    from claude_tap.viewer import TOOL_BLOAT_MIN_BYTES, _detect_tool_bloat, _tool_result_text

    payload = {"value": "中" * 1666}  # ~5 KB unescaped, ~10 KB escaped
    text = _tool_result_text(payload)
    assert "\\u" not in text
    assert len(text.encode("utf-8")) < TOOL_BLOAT_MIN_BYTES
    assert _detect_tool_bloat([{"role": "user", "content": [{"type": "tool_result", "content": payload}]}]) is None

    # A genuinely oversized CJK payload is still caught.
    assert _detect_tool_bloat(
        [{"role": "user", "content": [{"type": "tool_result", "content": {"value": "中" * 4000}}]}]
    )


def test_a_gemini_function_response_measures_the_same_in_both_detectors() -> None:
    """The browser pretty-prints a structured `functionResponse.response`.

    `geminiFunctionResponseContent` calls `JSON.stringify(output, null, 2)`, so a
    one-line dump here measures a different payload than the detail view does: an
    array of 1,500 one-character strings is ~7.5 KB compact against ~10.5 KB
    indented. Only the second crosses the threshold, so the metadata scan would
    omit a badge the detail view still warns about.
    """
    import json

    from claude_tap.viewer import (
        TOOL_BLOAT_MIN_BYTES,
        _detect_tool_bloat,
        _extract_gemini_request_messages,
        _gemini_function_response_content,
    )

    output = ["x"] * 1500
    compact = json.dumps(output, ensure_ascii=False)
    rendered = _gemini_function_response_content({"response": {"output": output}})

    assert len(compact.encode("utf-8")) < TOOL_BLOAT_MIN_BYTES
    assert len(rendered.encode("utf-8")) >= TOOL_BLOAT_MIN_BYTES
    assert rendered == json.dumps(output, ensure_ascii=False, indent=2)

    body = {
        "contents": [
            {
                "role": "user",
                "parts": [{"functionResponse": {"name": "read", "response": {"output": output}}}],
            }
        ]
    }
    assert _detect_tool_bloat(_extract_gemini_request_messages(body)) is not None

    # A string payload is passed through untouched, and a missing one reads as
    # empty rather than as the text "null", which JS would never produce.
    assert _gemini_function_response_content({"response": {"output": "plain"}}) == "plain"
    assert _gemini_function_response_content({"response": {"output": None}}) == ""
    assert _gemini_function_response_content({"response": None}) == ""
    assert _gemini_function_response_content({}) == ""

    # Non-ASCII stays unescaped, matching JSON.stringify, so a CJK result is not
    # inflated past the threshold on this side alone.
    assert "\\u" not in _gemini_function_response_content({"response": {"output": {"值": "中"}}})


def test_a_gemini_response_keeps_the_fields_beside_output() -> None:
    """`output` stands for the response only when it is the whole response.

    A sibling field beside it is result data the model was given, and unwrapping
    dropped it from the display and from the measurement together:
    `{"output": "ok", "logs": <25 KB>}` showed two bytes and earned no badge.
    Widening only the bloat payload would trade that for the opposite bug, a badge
    whose bytes the reader cannot find, so both sides read the same object.
    """
    from claude_tap.viewer import (
        TOOL_BLOAT_MIN_BYTES,
        _detect_tool_bloat,
        _extract_gemini_request_messages,
        _gemini_function_response_content,
    )

    # A lone `output` is still unwrapped, at every value type.
    assert _gemini_function_response_content({"response": {"output": "plain"}}) == "plain"
    assert _gemini_function_response_content({"response": {"output": None}}) == ""
    assert _gemini_function_response_content({"response": {"output": ["a"]}}) == '[\n  "a"\n]'

    # With a sibling the whole object is kept, so the siblings stay measurable.
    both = _gemini_function_response_content({"response": {"output": "ok", "logs": "L" * 25000}})
    assert '"logs"' in both
    assert len(both.encode("utf-8")) >= TOOL_BLOAT_MIN_BYTES
    # Even when `output` itself is empty, which the one-key check must not confuse.
    assert '"err"' in _gemini_function_response_content({"response": {"output": None, "err": "boom"}})

    part = {"functionResponse": {"name": "read", "response": {"output": "ok", "logs": "L" * 25000}}}
    body = {"contents": [{"role": "user", "parts": [part]}]}
    bloat = _detect_tool_bloat(_extract_gemini_request_messages(body, for_bloat=True))
    assert bloat is not None
    assert bloat["byte_count"] >= TOOL_BLOAT_MIN_BYTES
    # The badge measures exactly the string the opened entry displays.
    displayed = _extract_gemini_request_messages(body)[0]["content"][0]["content"]
    assert displayed == both


def test_integral_floats_serialize_like_json_stringify() -> None:
    """JSON.stringify writes 1.0 as 1; json.dumps does not.

    2,500 such numbers are ~10 KB with the Python default and ~5 KB in the
    browser, so lazy metadata would badge a turn the opened entry then
    treats as clean.
    """
    from claude_tap.viewer import TOOL_BLOAT_MIN_BYTES, _bloat_json, _detect_tool_bloat

    assert _bloat_json({"n": 1.0}) == '{"n":1}'
    payload = [1.0] * 2500
    text = _bloat_json(payload)
    assert "1.0" not in text
    assert len(text.encode("utf-8")) < TOOL_BLOAT_MIN_BYTES
    assert _detect_tool_bloat([{"role": "user", "content": [{"type": "tool_result", "content": payload}]}]) is None


def test_numbers_outside_pythons_positional_range_match_json_stringify() -> None:
    """The two runtimes switch to exponent notation at different magnitudes.

    ECMA-262 stays positional out to 21 integer digits and 6 leading zeros;
    Python's repr switches at 17 and 5. Casting integral floats to int fixed
    ``1.0`` but inflated the other end: ``int(1e21)`` writes 22 digits where the
    browser writes five characters, so 500 such values measured 11,501 bytes in
    the sidebar against 3,001 in the opened entry.
    """
    from claude_tap.viewer import TOOL_BLOAT_MIN_BYTES, _bloat_json, _detect_tool_bloat

    assert _bloat_json({"n": 1e21}) == '{"n":1e+21}'
    assert _bloat_json({"n": 1e16}) == '{"n":10000000000000000}'
    assert _bloat_json({"n": 1e-7}) == '{"n":1e-7}'
    assert _bloat_json({"n": 1e-6}) == '{"n":0.000001}'
    assert _bloat_json({"n": -0.0}) == '{"n":0}'
    # NaN and the infinities are not JSON; JSON.stringify writes null.
    assert _bloat_json([float("nan"), float("inf"), float("-inf")]) == "[null,null,null]"

    payload = [1e21] * 500
    assert len(_bloat_json(payload).encode("utf-8")) < TOOL_BLOAT_MIN_BYTES
    assert _detect_tool_bloat([{"role": "user", "content": [{"type": "tool_result", "content": payload}]}]) is None


def test_json_integers_are_measured_as_the_doubles_js_parses() -> None:
    """`JSON.parse` has only doubles; `json.loads` keeps arbitrary precision.

    So a big literal is written digit for digit here and in exponent form there:
    500 copies of ``999999999999999999999999`` measured 12,507 bytes in the
    sidebar against 3,007 in the opened entry, and ``9007199254740993`` kept a
    digit the browser had already rounded away.
    """
    from claude_tap.viewer import TOOL_BLOAT_MIN_BYTES, _bloat_json, _detect_tool_bloat

    assert _bloat_json({"n": 999999999999999999999999}) == '{"n":1e+24}'
    assert _bloat_json({"n": 9007199254740993}) == '{"n":9007199254740992}'
    # Small integers are exact in a double and stay as they are.
    assert _bloat_json({"n": 12345}) == '{"n":12345}'
    assert _bloat_json({"n": -0}) == '{"n":0}'
    # Past the double range JSON.parse yields Infinity, which stringify nulls.
    assert _bloat_json({"n": 10**400}) == '{"n":null}'
    # bool is an int subclass; it must stay true/false, not become 1/0.
    assert _bloat_json({"t": True, "f": False}) == '{"t":true,"f":false}'

    payload = json.loads("[" + ",".join(["999999999999999999999999"] * 500) + "]")
    assert len(_bloat_json(payload).encode("utf-8")) < TOOL_BLOAT_MIN_BYTES
    assert _detect_tool_bloat([{"role": "user", "content": [{"type": "tool_result", "content": payload}]}]) is None


def test_lone_surrogates_are_escaped_like_json_stringify() -> None:
    """`JSON.stringify` has been well-formed since ES2019: a partnerless code
    unit is escaped as six ASCII characters, not emitted raw.

    Left raw with ``ensure_ascii=False``, each one folds into a 3-byte U+FFFD
    when the size is measured, so 2,000 of them came to 6,012 bytes here against
    12,012 in the browser -- the sidebar dropped a badge the opened entry showed.
    """
    from claude_tap.viewer import TOOL_BLOAT_MIN_BYTES, _bloat_json, _detect_tool_bloat, _text_size_bytes

    assert _bloat_json({"v": "\ud800"}) == '{"v":"\\ud800"}'
    assert _bloat_json({"v": "\udfff"}) == '{"v":"\\udfff"}'
    # A well-formed pair is one astral character, which the browser writes as
    # itself; only unpaired units are escaped.
    assert _bloat_json({"v": "\U00010000"}) == '{"v":"\U00010000"}'
    assert _bloat_json({"v": 'a"b\x01'}) == '{"v":"a\\"b\\u0001"}'

    payload = {"value": "\ud800" * 2000}
    assert _text_size_bytes(_bloat_json(payload)) > TOOL_BLOAT_MIN_BYTES
    assert _detect_tool_bloat([{"role": "user", "content": [{"type": "tool_result", "content": payload}]}]) is not None


def test_gemini_function_responses_are_sized_separately() -> None:
    """One Gemini content item can carry several functionResponse parts.

    Display renders each as its own block. Measuring the wrapped list as one
    result would badge two 6 KB replies that the opened entry then shows
    without a warning.
    """
    from claude_tap.viewer import TOOL_BLOAT_MIN_BYTES, _detect_tool_bloat, _extract_request_messages

    mid = "x" * 6000
    body = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"functionResponse": {"name": "a", "response": {"output": mid}}},
                    {"functionResponse": {"name": "b", "response": {"output": "y" * 6000}}},
                ],
            }
        ]
    }
    msgs = _extract_request_messages(body, for_bloat=True)
    assert msgs[0]["role"] != "tool"
    assert _detect_tool_bloat(msgs) is None

    bloated = _detect_tool_bloat(
        _extract_request_messages(
            {
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {"functionResponse": {"name": "a", "response": {"output": "z" * TOOL_BLOAT_MIN_BYTES}}},
                            {"functionResponse": {"name": "b", "response": {"output": mid}}},
                        ],
                    }
                ]
            },
            for_bloat=True,
        )
    )
    assert bloated is not None
    assert bloated["count"] == 1


def test_a_function_call_output_is_sized_by_its_output_field() -> None:
    """`function_call_output` keeps its payload in `output`, not in `content`.

    Reading `content` sizes every one of them as empty, so an oversized
    Responses-shaped result would never be flagged from either scan.
    """
    from claude_tap.viewer import _detect_tool_bloat

    for block_type in ("function_call_output", "computer_call_output", "custom_tool_call_output"):
        found = _detect_tool_bloat([{"role": "tool", "content": [{"type": block_type, "output": "z" * 25000}]}])
        assert found is not None, block_type
        assert found["count"] == 1

    # `content` still works where a trace carries it there instead.
    assert _detect_tool_bloat([{"role": "tool", "content": [{"type": "function_call_output", "content": "z" * 25000}]}])


def test_an_array_valued_tool_role_payload_is_scanned() -> None:
    """The viewer wraps any tool-role content in a tool_result before rendering.

    For traces past the lazy-loading threshold the badge comes only from this
    scan, so a list payload that it skipped would show a warning in the detail
    view with no badge on the row.
    """
    from claude_tap.viewer import _detect_tool_bloat, _text_size_bytes, _tool_result_text

    # A list of bare strings and a list of text blocks are both results.
    assert _detect_tool_bloat([{"role": "tool", "content": ["z" * 25000]}]) is not None
    assert _detect_tool_bloat([{"role": "tool", "content": [{"type": "text", "text": "z" * 25000}]}]) is not None

    # Display wraps any tool-role list in one outer tool_result, so a pre-wrapped
    # pair is one combined payload rather than two separately counted results.

    wrapped = [
        {"type": "tool_result", "content": "z" * 25000},
        {"type": "tool_result", "content": "y" * 30000},
    ]
    combined = _detect_tool_bloat([{"role": "tool", "content": wrapped}])
    assert combined is not None
    assert combined["count"] == 1
    assert combined["byte_count"] == _text_size_bytes(_tool_result_text(wrapped))

    # An image-only list stays unflagged, as its bytes are not context text.
    assert (
        _detect_tool_bloat(
            [
                {
                    "role": "tool",
                    "content": [{"type": "input_image", "image_url": "data:image/png;base64," + "x" * 50000}],
                }
            ]
        )
        is None
    )


def test_a_normalized_computer_screenshot_is_not_counted_as_text() -> None:
    """A screenshot handed back from a computer-use call is an image.

    Serializing it to a string would count the base64 as result text, badging
    the turn for a payload that is billed by dimension instead.
    """
    from claude_tap.viewer import _detect_tool_bloat, _extract_request_messages

    data_url = "data:image/png;base64," + "x" * 60000
    body = {
        "input": [
            {
                "type": "computer_call_output",
                "call_id": "call_1",
                "output": {"type": "computer_screenshot", "image_url": data_url},
            }
        ]
    }
    msgs = _extract_request_messages(body)
    assert msgs == [{"role": "tool", "content": [{"type": "input_image", "image_url": data_url}]}]
    assert _detect_tool_bloat(msgs) is None

    # A textual output from the same item shape is still measured.
    text_body = {"input": [{"type": "computer_call_output", "call_id": "c", "output": "z" * 25000}]}
    assert _detect_tool_bloat(_extract_request_messages(text_body)) is not None


def test_lone_surrogates_match_text_encoder_replacement_bytes() -> None:
    """TextEncoder turns an unpaired surrogate into U+FFFD (three UTF-8 bytes).

    Python's utf-8 replace would emit a one-byte `?`, so a lazy scan of
    `\\ud800` * 4000 would stay under the threshold while the opened entry
    crosses it.
    """
    from claude_tap.viewer import _detect_tool_bloat, _text_size_bytes

    lone = "\ud800" * 4000
    assert _text_size_bytes(lone) == 12000
    assert _detect_tool_bloat([{"role": "tool", "content": lone}]) is not None


def test_a_domain_image_field_is_still_measured_as_text() -> None:
    """A container tag named `image` is not an image block.

    Skipping any truthy `image` key would hide a textual result that still
    consumes context tokens.
    """
    from claude_tap.viewer import _detect_tool_bloat, _is_image_payload

    payload = {"image": "myorg/app:latest", "logs": "z" * 25000}
    assert _is_image_payload(payload) is False
    found = _detect_tool_bloat([{"role": "tool", "content": payload}])
    assert found is not None
    assert found["byte_count"] >= 25000


def test_tool_search_output_bloat_uses_raw_tool_definitions() -> None:
    """The display summary keeps only names; the detector must size the schemas.

    A tool_search_output with short names and large input schemas would otherwise
    look tiny in lazy metadata and in the opened banner.
    """
    from claude_tap.viewer import (
        _detect_tool_bloat,
        _extract_metadata_from_record,
        _extract_request_messages,
        _tool_search_output_content,
    )

    schema = "z" * 20000
    tools = [
        {
            "type": "namespace",
            "name": "mcp__codex_apps__figma",
            "tools": [
                {
                    "type": "function",
                    "name": "_use_figma",
                    "description": schema,
                    "input_schema": {"type": "object", "properties": {"q": {"description": schema}}},
                }
            ],
        }
    ]
    body = {"input": [{"type": "tool_search_output", "call_id": "call_search", "tools": tools}]}
    display = _extract_request_messages(body)
    assert "tool_search_output" in display[0]["content"]
    assert len(_tool_search_output_content(body["input"][0])) < 200
    assert _detect_tool_bloat(display) is None

    raw = _extract_request_messages(body, for_bloat=True)
    assert raw[0]["content"] == tools
    found = _detect_tool_bloat(raw)
    assert found is not None
    assert found["count"] == 1

    record = {
        "turn": 1,
        "request_id": "req_search",
        "timestamp": "2026-03-17T00:00:00Z",
        "duration_ms": 10,
        "request": {"method": "POST", "path": "/v1/responses", "body": body},
        "response": {"status": 200, "body": {}},
    }
    meta = _extract_metadata_from_record(record)
    assert meta is not None
    assert meta["tool_bloat"] is not None
    assert meta["tool_bloat"]["count"] == 1
