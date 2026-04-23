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


def test_viewer_labels_codex_request_input_as_context_when_response_output_missing(
    responses_page,
) -> None:
    responses_page.evaluate(
        """() => {
          entries[0].request.path = '/backend-api/codex/responses';
          entries[0].request.body = {
            model: 'gpt-5.4',
            instructions: 'You are Codex, a coding agent.',
            input: [
              { type: 'message', role: 'developer', content: [{ type: 'input_text', text: 'developer policy' }] },
              { type: 'message', role: 'user', content: [{ type: 'input_text', text: 'first user question' }] },
              { type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'prior assistant answer' }] },
              { type: 'reasoning', summary: [{ type: 'summary_text', text: 'hidden reasoning' }] },
              { type: 'function_call', name: 'exec_command', arguments: '{\"cmd\":\"pwd\"}' },
              { type: 'function_call_output', call_id: 'call_1', output: 'ok' },
              { type: 'message', role: 'user', content: [{ type: 'input_text', text: 'latest user question' }] },
              { type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'second prior assistant answer' }] }
            ]
          };
          entries[0].response.body = {
            status: 'completed',
            output: [],
            usage: { input_tokens: 12, output_tokens: 0 }
          };
          entries[0].response.ws_events = [];
          renderDetail(entries[0]);
        }"""
    )

    section_titles = responses_page.locator("#detail .section .title").all_inner_texts()
    detail_text = responses_page.locator("#detail").inner_text()
    response_text = responses_page.locator("#detail .section").nth(2).inner_text()

    assert "Messages" not in section_titles
    assert "Request Context" in section_titles
    assert "No response output captured; showing request context only." in detail_text
    assert "prior assistant answer" in detail_text
    assert "second prior assistant answer" in detail_text
    assert "No response output captured; showing request context only." in response_text
