"""Browser coverage for OpenAI Responses traces in viewer.html."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from claude_tap.viewer import _generate_html_viewer

pw_missing = False
try:
    from playwright.sync_api import sync_playwright  # noqa: F401
except ImportError:
    pw_missing = True

pytestmark = pytest.mark.skipif(pw_missing, reason="playwright not installed")


@pytest.fixture(scope="module")
def responses_html_file() -> Path:
    trace_path = Path(__file__).parent / "fixtures" / "openai_responses_trace.jsonl"
    html_path = Path(tempfile.mktemp(suffix=".html"))
    _generate_html_viewer(trace_path, html_path)
    yield html_path
    html_path.unlink(missing_ok=True)


@pytest.fixture(scope="module")
def responses_page(responses_html_file: Path):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"file://{responses_html_file}", timeout=10000)
        page.wait_for_selector(".sidebar-item", timeout=5000)
        yield page
        browser.close()


def test_viewer_renders_codex_responses_messages_usage_and_response(responses_page) -> None:
    responses_page.locator(".sidebar-item").first.click()
    responses_page.wait_for_selector("#detail .section", timeout=5000)

    detail_text = responses_page.locator("#detail").inner_text()

    assert "Messages" in detail_text
    assert "USER" in detail_text
    assert "Hello" in detail_text
    assert "Response" in detail_text
    assert "Hello! How can I help?" in detail_text
    assert "500" in detail_text
    assert "10" in detail_text


def test_viewer_omits_empty_reasoning_blocks_for_zero_reasoning_tokens(responses_page) -> None:
    responses_page.evaluate(
        """() => {
          entries[0].response.body = {
            output: [
              { type: 'reasoning', summary: [{ type: 'summary_text', text: '' }] },
              { type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'Visible answer' }] }
            ],
            usage: { input_tokens: 1, output_tokens: 1, reasoning_tokens: 0 }
          };
          renderDetail(entries[0]);
        }"""
    )

    detail_text = responses_page.locator("#detail").inner_text()

    assert "Visible answer" in detail_text
    assert "thinking" not in detail_text.lower()


def test_viewer_reconstructs_ws_output_from_output_item_done_when_completed_output_is_empty(
    responses_page,
) -> None:
    responses_page.evaluate(
        """() => {
          entries[0].response.body = { status: 'completed', output: [], usage: { input_tokens: 1, output_tokens: 1 } };
          entries[0].response.ws_events = [
            { type: 'response.created', response: { id: 'resp_1', status: 'in_progress' } },
            { type: 'response.output_item.done', output_index: 0, item: { type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'Recovered from ws_events' }] } },
            { type: 'response.completed', response: { id: 'resp_1', status: 'completed', output: [], usage: { input_tokens: 1, output_tokens: 1 } } }
          ];
          renderDetail(entries[0]);
        }"""
    )

    detail_text = responses_page.locator("#detail").inner_text()

    assert "Recovered from ws_events" in detail_text


def test_viewer_warns_for_empty_input_responses_continuation(responses_page) -> None:
    responses_page.evaluate(
        """() => {
          entries[0].request.headers = {
            session_id: 'session_abc',
            version: '0.122.0-alpha.1'
          };
          entries[0].request.body = {
            type: 'response.create',
            model: 'gpt-5.5',
            instructions: 'You are Codex.',
            input: [],
            prompt_cache_key: 'cache_abc'
          };
          entries[0].response.body = {
            id: 'resp_current',
            previous_response_id: 'resp_previous',
            output: [
              { type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'Continuation answer' }] }
            ],
            usage: { input_tokens: 2, output_tokens: 3 }
          };
          renderDetail(entries[0]);
        }"""
    )

    detail_text = responses_page.locator("#detail").inner_text()

    assert "Stateful Responses continuation" in detail_text
    assert "previous_response_id but no captured user message history" in detail_text
    assert "resp_previous" in detail_text
    assert "cache_abc" in detail_text
    assert "0.122.0-alpha.1" in detail_text
    assert "Continuation answer" in detail_text


def test_viewer_warns_for_top_level_responses_continuation_payload(responses_page) -> None:
    responses_page.evaluate(
        """() => {
          entries[0] = {
            turn: 1,
            request_id: 'req_top_level',
            request: {
              method: 'WEBSOCKET',
              path: '/v1/responses',
              headers: {},
              body: {
                type: 'response.create',
                model: 'gpt-5.5',
                input: [],
                prompt_cache_key: 'cache_top_level'
              }
            },
            response: {
              id: 'resp_top_current',
              previous_response_id: 'resp_top_previous',
              output: [
                { type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'Top-level answer' }] }
              ]
            }
          };
          renderDetail(entries[0]);
        }"""
    )

    detail_text = responses_page.locator("#detail").inner_text()

    assert "Stateful Responses continuation" in detail_text
    assert "resp_top_previous" in detail_text
    assert "cache_top_level" in detail_text
    assert "Top-level answer" in detail_text


def test_viewer_warns_for_tool_result_only_responses_continuation(responses_page) -> None:
    responses_page.evaluate(
        """() => {
          entries[0].request.body = {
            type: 'response.create',
            model: 'gpt-5.5',
            instructions: 'You are Codex.',
            input: [
              {
                type: 'function_call_output',
                call_id: 'call_123',
                output: 'name = "claude-tap"'
              }
            ],
            prompt_cache_key: 'cache_tool_result'
          };
          entries[0].response.body = {
            id: 'resp_tool_current',
            previous_response_id: 'resp_tool_previous',
            output: [
              { type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'claude-tap' }] }
            ],
            usage: { input_tokens: 2, output_tokens: 3 }
          };
          renderDetail(entries[0]);
        }"""
    )

    detail_text = responses_page.locator("#detail").inner_text()

    assert "Stateful Responses continuation" in detail_text
    assert "previous_response_id but no captured user message history" in detail_text
    assert "resp_tool_previous" in detail_text
    assert "cache_tool_result" in detail_text
    assert "claude-tap" in detail_text


def test_viewer_backfills_messages_from_previous_response_id(tmp_path) -> None:
    trace_path = tmp_path / "responses-continuation.jsonl"
    html_path = tmp_path / "responses-continuation.html"
    records = [
        {
            "turn": 1,
            "request_id": "req_prev",
            "request": {
                "method": "WEBSOCKET",
                "path": "/responses",
                "body": {
                    "type": "response.create",
                    "model": "gpt-5.5",
                    "input": [{"role": "user", "content": [{"type": "input_text", "text": "First prompt from trace"}]}],
                },
            },
            "response": {
                "status": 101,
                "body": {
                    "id": "resp_prev",
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "First answer"}],
                        }
                    ],
                },
            },
        },
        {
            "turn": 2,
            "request_id": "req_next",
            "request": {
                "method": "WEBSOCKET",
                "path": "/responses",
                "body": {
                    "type": "response.create",
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
            "response": {
                "status": 101,
                "body": {
                    "id": "resp_next",
                    "previous_response_id": "resp_prev",
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "Second answer"}],
                        }
                    ],
                },
            },
        },
    ]
    trace_path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
    _generate_html_viewer(trace_path, html_path)

    html = html_path.read_text(encoding="utf-8")

    assert html.count("First prompt from trace") == 2
    assert html.count("First answer") == 2
    assert "function_call_output" in html
    assert "Second answer" in html


def test_viewer_renders_responses_function_history(responses_page) -> None:
    responses_page.evaluate(
        """() => {
          entries[0].request.body = {
            type: 'response.create',
            model: 'gpt-5.5',
            input: [
              {
                type: 'message',
                role: 'assistant',
                content: [{ type: 'output_text', text: 'I will inspect the directory' }]
              },
              {
                type: 'function_call',
                call_id: 'call_1',
                name: 'exec_command',
                arguments: '{"cmd":"pwd"}'
              },
              {
                type: 'function_call_output',
                call_id: 'call_1',
                output: '/tmp/project'
              }
            ]
          };
          entries[0].response.body = { id: 'resp_tool_history', output: [] };
          renderDetail(entries[0]);
        }"""
    )
    text = responses_page.locator("#detail").inner_text()

    assert "exec_command" in text
    assert "pwd" in text
    assert "/tmp/project" in text
    assert responses_page.locator("#detail .msg.assistant").count() == 1
    assert responses_page.locator("#detail .msg.tool_result").count() == 1


def test_viewer_merges_consecutive_responses_assistant_items(responses_page) -> None:
    responses_page.evaluate(
        """() => {
          entries[0].request.body = {
            type: 'response.create',
            model: 'gpt-5.5',
            input: [
              {
                type: 'message',
                role: 'user',
                content: [{ type: 'input_text', text: 'Analyze this workbook' }]
              },
              {
                type: 'message',
                role: 'assistant',
                content: [{ type: 'output_text', text: 'I will inspect the file.' }]
              },
              {
                type: 'function_call',
                call_id: 'call_1',
                name: 'exec_command',
                arguments: '{"cmd":"ls -l /home/user"}'
              },
              {
                type: 'message',
                role: 'assistant',
                content: [{ type: 'output_text', text: 'Then I will read workbook metadata.' }]
              },
              {
                type: 'function_call',
                call_id: 'call_2',
                name: 'exec_command',
                arguments: '{"cmd":"python3 inspect.py"}'
              }
            ]
          };
          entries[0].response.body = { id: 'resp_consecutive_assistant', output: [] };
          renderDetail(entries[0]);
        }"""
    )
    text = responses_page.locator("#detail").inner_text()

    assert responses_page.locator("#detail .msg.user").count() == 1
    assert responses_page.locator("#detail .msg.assistant").count() == 1
    assert "I will inspect the file." in text
    assert "ls -l /home/user" in text
    assert "Then I will read workbook metadata." in text
    assert "python3 inspect.py" in text


def test_viewer_orders_context_messages_stream_and_json(responses_page) -> None:
    responses_page.evaluate(
        """() => {
          entries[0].request.body = {
            type: 'response.create',
            model: 'gpt-5.5',
            instructions: 'LONG_SYSTEM_PROMPT\\n' + 'system details\\n'.repeat(200),
            input: [
              {
                type: 'message',
                role: 'user',
                content: [{ type: 'input_text', text: 'VISIBLE_USER_REQUEST' }]
              }
            ],
            tools: [{ type: 'function', name: 'exec_command' }]
          };
          entries[0].response.body = {
            id: 'resp_order',
            output: [
              { type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'VISIBLE_ASSISTANT_RESPONSE' }] }
            ],
            usage: { input_tokens: 1, output_tokens: 1 }
          };
          entries[0].response.sse_events = [
            { event: 'response.completed', data: { response: { id: 'resp_order', output: [] } } }
          ];
          renderDetail(entries[0]);
        }"""
    )
    text = responses_page.locator("#detail").inner_text()

    assert text.index("Tools") < text.index("System Prompt")
    assert text.index("System Prompt") < text.index("Messages")
    assert text.index("Messages") < text.index("Response")
    assert text.index("Response") < text.index("SSE Events")
    assert text.index("SSE Events") < text.index("Full JSON")
    assert text.index("LONG_SYSTEM_PROMPT") < text.index("VISIBLE_USER_REQUEST")
