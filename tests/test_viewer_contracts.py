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
    assert result["roles"] == list(case.expected_roles)
    assert result["tools"] == list(case.expected_tools)
    assert result["outputTypes"] == list(case.expected_output_types)
    for key, value in case.expected_usage.items():
        assert result["usage"][key] == value
    assert result["eventCount"] >= case.min_stream_events
    for text in case.required_detail_text:
        assert text in result["detailText"]


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
