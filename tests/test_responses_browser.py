"""Browser coverage for OpenAI Responses traces in viewer.html."""

from __future__ import annotations

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
