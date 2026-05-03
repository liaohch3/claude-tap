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


@pytest.fixture()
def responses_page(responses_html_file: Path):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"file://{responses_html_file}", timeout=10000)
        page.wait_for_selector(".sidebar-item", timeout=5000)
        yield page
        browser.close()


@pytest.fixture(scope="module")
def codex_ws_multi_html_file() -> Path:
    trace_path = Path(__file__).parent / "fixtures" / "codex_ws_multi_response_trace.jsonl"
    html_path = Path(tempfile.mktemp(suffix=".html"))
    _generate_html_viewer(trace_path, html_path)
    yield html_path
    html_path.unlink(missing_ok=True)


@pytest.fixture()
def codex_ws_multi_page(codex_ws_multi_html_file: Path):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"file://{codex_ws_multi_html_file}", timeout=10000)
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
    responses_page.locator(".section-header", has_text="Tools").click()
    tools_text = responses_page.locator(".section", has_text="Tools").first.inner_text()
    assert "exec_command" in tools_text
    assert "web_search" in tools_text
    assert "unknown" not in tools_text.lower()


def test_viewer_treats_codex_forward_websocket_path_as_primary(responses_page) -> None:
    result = responses_page.evaluate(
        """() => ({
          tier: pathTier('/backend-api/codex/responses'),
          primary: isPathPrimary('/backend-api/codex/responses')
        })"""
    )

    assert result == {"tier": 0, "primary": True}


def test_viewer_expands_codex_websocket_session_into_response_entries(codex_ws_multi_page) -> None:
    result = codex_ws_multi_page.evaluate(
        """() => ({
          entries: entries.length,
          derived: entries.filter(e => e.derived_from_websocket).length,
          sidebar: document.querySelectorAll('.sidebar-item').length,
          banners: document.querySelectorAll('.continuation-banner').length,
          turns: entries.map(e => e.turn),
          previousIds: entries.map(e => e.request.body.previous_response_id || ''),
          responseIds: entries.map(e => e.response.body.id || ''),
          hasPrompt: entries.map(e => JSON.stringify(e.request.body).includes('你好，调用一个工具，然后结束')),
          usage: entries.map(e => getUsage(e)?.total_tokens || 0),
          messages: entries.map(e => getMessages(e.request.body).map(m => m.role)),
          responseTypes: entries.map(e => (getResponseOutput(e)?.content || []).map(c => c.type))
        })"""
    )

    assert result == {
        "entries": 2,
        "derived": 2,
        "sidebar": 2,
        "banners": 0,
        "turns": ["14.1", "14.2"],
        "previousIds": ["resp_prefetch", "resp_tool"],
        "responseIds": ["resp_tool", "resp_final"],
        "hasPrompt": [True, True],
        "usage": [24, 35],
        "messages": [["developer", "user", "user"], ["developer", "user", "user", "assistant", "tool"]],
        "responseTypes": [["tool_use"], ["text"]],
    }

    codex_ws_multi_page.locator(".sidebar-item").nth(0).click()
    tool_call_detail = codex_ws_multi_page.locator("#detail").inner_text()
    assert "Sanitized project rules." in tool_call_detail
    assert "你好，调用一个工具，然后结束" in tool_call_detail
    assert "exec_command" in tool_call_detail
    assert "FINAL_OK" not in tool_call_detail

    codex_ws_multi_page.locator(".sidebar-item").nth(1).click()
    final_detail = codex_ws_multi_page.locator("#detail").inner_text()
    assert "Sanitized project rules." in final_detail
    assert "你好，调用一个工具，然后结束" in final_detail
    assert "/workspace/project" in final_detail
    assert "exec_command" in final_detail
    assert "FINAL_OK" in final_detail
    assert "Responses continuation" not in final_detail
    assert "有状态 Responses 续接" not in final_detail


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
