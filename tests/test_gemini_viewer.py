from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from claude_tap.usage import normalize_usage
from claude_tap.viewer import _extract_metadata, _extract_request_messages, _generate_html_viewer

pw_missing = False
try:
    from playwright.sync_api import sync_playwright  # noqa: F401
except ImportError:
    pw_missing = True


def _sse_frame(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _gemini_record() -> dict:
    return {
        "timestamp": "2026-05-13T12:00:00+00:00",
        "request_id": "req_gemini",
        "turn": 1,
        "duration_ms": 1234,
        "request": {
            "method": "POST",
            "path": "/v1internal:streamGenerateContent?alt=sse",
            "headers": {"Host": "cloudcode-pa.googleapis.com"},
            "body": {
                "model": "gemini-3-flash-preview",
                "project": "test-project",
                "request": {
                    "systemInstruction": {
                        "role": "user",
                        "parts": [
                            {
                                "text": "You are Gemini CLI, an autonomous CLI agent specializing in software engineering tasks."
                            }
                        ],
                    },
                    "contents": [
                        {
                            "role": "user",
                            "parts": [
                                {"text": "<session_context>Workspace: /repo</session_context>"},
                                {"text": "Use shell to inspect the workspace."},
                            ],
                        },
                        {
                            "role": "model",
                            "parts": [
                                {
                                    "functionCall": {
                                        "name": "run_shell_command",
                                        "args": {"command": "pwd", "description": "Check current directory."},
                                    }
                                }
                            ],
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
                                        "required": ["command"],
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
            "body": (
                _sse_frame(
                    {
                        "response": {
                            "candidates": [
                                {
                                    "content": {
                                        "role": "model",
                                        "parts": [
                                            {"thought": True, "text": "I need to run a shell command."},
                                            {
                                                "functionCall": {
                                                    "name": "run_shell_command",
                                                    "args": {"command": "pwd"},
                                                }
                                            },
                                        ],
                                    }
                                }
                            ],
                            "usageMetadata": {
                                "promptTokenCount": 100,
                                "candidatesTokenCount": 8,
                                "cachedContentTokenCount": 40,
                            },
                        }
                    }
                )
                + _sse_frame(
                    {
                        "response": {
                            "candidates": [
                                {"content": {"role": "model", "parts": [{"text": "Final OK from Gemini."}]}}
                            ],
                            "usageMetadata": {
                                "promptTokenCount": 110,
                                "candidatesTokenCount": 12,
                                "cachedContentTokenCount": 40,
                            },
                        }
                    }
                )
            ),
        },
    }


def test_normalize_usage_maps_gemini_usage_metadata() -> None:
    usage = normalize_usage(
        {
            "promptTokenCount": 110,
            "candidatesTokenCount": 12,
            "cachedContentTokenCount": 40,
        }
    )

    assert usage["input_tokens"] == 110
    assert usage["output_tokens"] == 12
    assert usage["cache_read_input_tokens"] == 40


def test_extract_request_messages_normalizes_gemini_contents() -> None:
    messages = _extract_request_messages(_gemini_record()["request"]["body"])

    assert [message["role"] for message in messages] == ["user", "assistant", "tool"]
    assert messages[0]["content"][0]["text"].startswith("<session_context>")
    assert messages[1]["content"][0] == {
        "type": "tool_use",
        "id": "",
        "name": "run_shell_command",
        "input": {"command": "pwd", "description": "Check current directory."},
    }
    assert messages[2]["content"][0]["type"] == "tool_result"
    assert messages[2]["content"][0]["tool_use_id"] == "run_shell_command_1"
    assert "Output: /repo" in messages[2]["content"][0]["content"]


def test_extract_metadata_understands_gemini_system_tools_output_and_usage() -> None:
    meta = _extract_metadata(json.dumps(_gemini_record(), ensure_ascii=False))

    assert meta is not None
    assert meta["model"] == "gemini-3-flash-preview"
    assert meta["has_system"] is True
    assert meta["sys_hint"].startswith("You are Gemini CLI")
    assert meta["message_count"] == 3
    assert meta["tool_names"] == ["run_shell_command"]
    assert meta["response_tool_names"] == ["run_shell_command"]
    assert meta["input_tokens"] == 110
    assert meta["output_tokens"] == 12
    assert meta["cache_read_input_tokens"] == 40


@pytest.fixture(scope="module")
def gemini_html_file() -> Path:
    trace_path = Path(tempfile.mktemp(suffix=".jsonl"))
    html_path = Path(tempfile.mktemp(suffix=".html"))
    trace_path.write_text(json.dumps(_gemini_record(), ensure_ascii=False) + "\n", encoding="utf-8")
    _generate_html_viewer(trace_path, html_path)
    yield html_path
    trace_path.unlink(missing_ok=True)
    html_path.unlink(missing_ok=True)


@pytest.mark.skipif(pw_missing, reason="playwright not installed")
def test_viewer_renders_gemini_semantic_sections(gemini_html_file: Path) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"file://{gemini_html_file}", timeout=10000)
        page.wait_for_selector(".sidebar-item", timeout=5000)
        page.locator(".sidebar-item").first.click()
        page.wait_for_selector("#detail .section", timeout=5000)

        result = page.evaluate(
            """() => {
              const entry = entries[0];
              const body = entry.request.body;
              return {
                system: extractSystem(body),
                roles: getMessages(body).map(message => message.role),
                tools: getRequestTools(body).map(toolDisplayName),
                output: getResponseOutput(entry).content,
                usage: getUsage(entry),
                eventCount: getResponseEvents(entry).length,
                detail: document.querySelector('#detail').innerText,
              };
            }"""
        )
        browser.close()

    assert result["system"].startswith("You are Gemini CLI")
    assert result["roles"] == ["user", "assistant", "tool"]
    assert result["tools"] == ["run_shell_command"]
    assert [block["type"] for block in result["output"]] == ["thinking", "tool_use", "text"]
    assert result["output"][1]["name"] == "run_shell_command"
    assert result["usage"]["input_tokens"] == 110
    assert result["usage"]["output_tokens"] == 12
    assert result["usage"]["cache_read_input_tokens"] == 40
    assert result["eventCount"] == 2
    detail = result["detail"]
    assert "System Prompt" in detail
    assert "Messages" in detail
    assert "Tools" in detail
    assert "Response" in detail
    assert "You are Gemini CLI" in detail
    assert "Use shell to inspect the workspace." in detail
    assert "Output: /repo" in detail
    assert "run_shell_command" in detail
    assert "Final OK from Gemini." in detail
