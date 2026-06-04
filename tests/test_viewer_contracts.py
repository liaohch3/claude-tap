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
        {"mode": "session", "label": "Session", "active": False},
    ]
    assert model_state["groups"] == ["aws.claude-opus-4.6", "aws.claude-sonnet-4.6", "other-model"]
    assert model_state["turns"] == ["Turn 3", "Turn 2", "Turn 1"]
    assert turn_state["buttons"] == [
        {"mode": "model", "label": "Model", "active": False},
        {"mode": "turn", "label": "Turn", "active": True},
        {"mode": "session", "label": "Session", "active": False},
    ]
    assert turn_state["groupCount"] == 0
    assert turn_state["turns"] == ["Turn 1", "Turn 2", "Turn 3"]
    assert session_state["buttons"] == [
        {"mode": "model", "label": "Model", "active": False},
        {"mode": "turn", "label": "Turn", "active": False},
        {"mode": "session", "label": "Session", "active": True},
    ]
    assert session_state["groups"] == [
        "Session 1 - First sidebar task",
        "Session 2 - Second sidebar task",
        "Session 3 - Second sidebar task",
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
        "Session 1 - Check configured MCP settings",
        "Session 2 - Diagnose portfolio holdings",
        "Session 3 - Add portfolio positions",
    ]
    assert state["counts"] == ["3", "2", "2"]
    assert state["turns"] == ["Turn 1", "Turn 2", "Turn 3", "Turn 4", "Turn 5", "Turn 6", "Turn 7"]
    assert "SUGGESTION MODE" not in state["headerText"]


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
              usesLazyMode: typeof EMBEDDED_TRACE_META !== 'undefined',
              sidebarTurns: Array.from(document.querySelectorAll('.sidebar-item .si-turn')).map(el => el.textContent),
              sidebarPaths: Array.from(document.querySelectorAll('.sidebar-item .si-path')).map(el => el.textContent),
              codexEntries: entries
                .filter(entry => entry.request?.path === '/v1/responses')
                .map(entry => ({
                  turn: entry.turn,
                  displayTurn: entry.display_turn ?? null,
                  captureTurn: entry.capture_turn ?? null,
                  generate: entry.request?.body?.generate ?? null,
                })),
            })"""
        )
    finally:
        page.close()

    assert errors == []
    assert state["usesLazyMode"] is True
    assert state["sidebarTurns"] == ["Turn 1", "Turn 2", "Turn 3"]
    assert state["sidebarPaths"] == ["WEBSOCKET /v1/responses", "WEBSOCKET /v1/responses", "WEBSOCKET /v1/responses"]
    assert state["codexEntries"][0] == {
        "turn": 2,
        "displayTurn": None,
        "captureTurn": 2,
        "generate": False,
    }
    assert state["codexEntries"][1]["displayTurn"] == 1
    assert state["codexEntries"][2]["displayTurn"] == 2
    assert state["codexEntries"][3]["displayTurn"] == 3


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
              usesLazyMode: typeof EMBEDDED_TRACE_META !== 'undefined',
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
    assert state["usesLazyMode"] is True
    assert state["sidebarTurns"] == ["Turn 1"]
    assert state["filteredRequestIds"] == ["req_event_visible"]
    assert state["eventEntries"] == [
        {
            "requestId": "req_event_prefetch",
            "displayTurn": None,
            "captureTurn": 1,
            "requestGenerate": False,
            "responseGenerate": False,
            "responseOutputCount": 0,
            "outputTokens": 0,
            "navigable": False,
        },
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
        "getUsage",
        "getResponseEvents",
        "getResponseOutput",
        "geminiMessages",
        "geminiResponseOutput",
        "renderTools",
        "renderImageBlock",
        "sessionTurnDiscriminator",
        "showSessionTooltip",
    }

    page = chromium_browser.new_page()
    page.add_init_script("window.__TRACE_SESSION_EXPORTS__ = {jsonl: 'coverage.jsonl', log: 'coverage.log'};")
    try:
        session = page.context.new_cdp_session(page)
        session.send("Profiler.enable")
        session.send("Profiler.startPreciseCoverage", {"callCount": True, "detailed": True})
        errors = _open_viewer_with_error_capture(page, html_path)

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
        "Session 1 - 继续",
        "Session 2 - 继续",
        "Session 3 - Analyze the flowchart",
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
