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

from claude_tap.viewer import _generate_html_viewer

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
                "content": [{"type": "text", "text": "Chat final OK."}],
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
            name="chat_completions",
            records=(_chat_completions_record(),),
            expected_sections=("Tools", "System Prompt", "Messages", "Response"),
            expected_system="Kimi contract system prompt.",
            expected_roles=("user", "assistant", "tool"),
            expected_tools=("read_file",),
            expected_output_types=("text",),
            expected_usage={"input_tokens": 150, "output_tokens": 10, "cache_read_input_tokens": 70},
            required_detail_text=("Read the project metadata.", "read_file", "Chat final OK."),
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
        ("req_order_unknown", 1, "other-model"),
        ("req_order_sonnet", 2, "aws.claude-sonnet-4.6"),
        ("req_order_opus", 3, "aws.claude-opus-4.6"),
    ]
    records = []
    for request_id, turn, model in rows:
        record = json.loads(json.dumps(base))
        record["request_id"] = request_id
        record["turn"] = turn
        record["timestamp"] = f"2026-05-13T13:2{turn}:00+00:00"
        record["request"]["body"]["model"] = model
        records.append(record)
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
              blockTitles: Array.from(document.querySelectorAll('#detail .trace-block-title span:first-child')).map(el => el.textContent),
              formats: Array.from(document.querySelectorAll('#detail .trace-format-btn')).map(el => ({
                format: el.dataset.format,
                label: el.textContent,
                active: el.classList.contains('active'),
              })),
              text: document.querySelector('#detail')?.innerText || '',
            })"""
        )

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


def test_viewer_sidebar_order_can_switch_between_model_groups_and_turn_sequence(
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
    finally:
        page.close()

    assert errors == []
    assert model_state["label"] == "Order"
    assert model_state["buttons"] == [
        {"mode": "model", "label": "Model", "active": True},
        {"mode": "turn", "label": "Turn", "active": False},
    ]
    assert model_state["groups"] == ["aws.claude-opus-4.6", "aws.claude-sonnet-4.6", "other-model"]
    assert model_state["turns"] == ["Turn 3", "Turn 2", "Turn 1"]
    assert turn_state["buttons"] == [
        {"mode": "model", "label": "Model", "active": False},
        {"mode": "turn", "label": "Turn", "active": True},
    ]
    assert turn_state["groupCount"] == 0
    assert turn_state["turns"] == ["Turn 1", "Turn 2", "Turn 3"]


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
    }

    page = chromium_browser.new_page()
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
