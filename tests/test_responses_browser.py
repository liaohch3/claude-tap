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


@pytest.fixture(scope="module")
def chat_completions_history_html_file() -> Path:
    trace_path = Path(tempfile.mktemp(suffix=".jsonl"))
    html_path = Path(tempfile.mktemp(suffix=".html"))
    record = {
        "request_id": "req_kimi_history",
        "turn": 1,
        "request": {
            "method": "POST",
            "path": "/chat/completions",
            "body": {
                "model": "kimi-k2-turbo-preview",
                "messages": [
                    {"role": "system", "content": "Kimi CLI regression system prompt."},
                    {"role": "user", "content": "Use a tool."},
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
                    {"role": "user", "content": "Continue in the same chat."},
                ],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "description": "Read a file.",
                            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                        },
                    }
                ],
            },
        },
        "response": {"status": 200, "body": {"content": [{"type": "text", "text": "Done."}]}},
    }
    trace_path.write_text(json.dumps(record) + "\n")
    _generate_html_viewer(trace_path, html_path)
    yield html_path
    trace_path.unlink(missing_ok=True)
    html_path.unlink(missing_ok=True)


@pytest.fixture()
def chat_completions_history_page(chat_completions_history_html_file: Path):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"file://{chat_completions_history_html_file}", timeout=10000)
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


def test_viewer_json_tree_toggle_collapses_and_expands(responses_page) -> None:
    responses_page.locator(".sidebar-item").first.click()
    responses_page.wait_for_selector("#detail .section", timeout=5000)

    result = responses_page.evaluate(
        """() => {
          const section = Array.from(document.querySelectorAll('#detail .section'))
            .find(el => el.querySelector('.title')?.textContent === 'Full JSON');
          if (!section) return { found: false };

          const toggle = section.querySelector('.jt-toggle');
          const children = section.querySelector('.jt-children');
          const summary = section.querySelector('.jt-summary');
          const closeLine = children?.nextElementSibling;
          const initial = {
            toggle: toggle?.textContent,
            childrenOpen: children?.classList.contains('jt-open'),
            summaryShown: summary?.classList.contains('jt-show'),
            closeHidden: closeLine?.classList.contains('jt-hidden'),
          };

          toggle.click();
          const collapsed = {
            toggle: toggle.textContent,
            childrenOpen: children.classList.contains('jt-open'),
            summaryShown: summary.classList.contains('jt-show'),
            closeHidden: closeLine.classList.contains('jt-hidden'),
          };

          toggle.click();
          const expanded = {
            toggle: toggle.textContent,
            childrenOpen: children.classList.contains('jt-open'),
            summaryShown: summary.classList.contains('jt-show'),
            closeHidden: closeLine.classList.contains('jt-hidden'),
          };

          return { found: true, initial, collapsed, expanded };
        }"""
    )

    assert result == {
        "found": True,
        "initial": {"toggle": "▼", "childrenOpen": True, "summaryShown": False, "closeHidden": False},
        "collapsed": {"toggle": "▶", "childrenOpen": False, "summaryShown": True, "closeHidden": True},
        "expanded": {"toggle": "▼", "childrenOpen": True, "summaryShown": False, "closeHidden": False},
    }


def test_viewer_maps_responses_cached_tokens_to_cache_read(responses_page) -> None:
    result = responses_page.evaluate(
        """() => getUsage({
          response: {
            body: {
              usage: {
                input_tokens: 11767,
                input_tokens_details: { cached_tokens: 11648 },
                output_tokens: 6,
                total_tokens: 11773
              }
            }
          }
        })"""
    )

    assert result["input_tokens"] == 11767
    assert result["output_tokens"] == 6
    assert result["cache_read_input_tokens"] == 11648


def test_viewer_renders_chat_completions_system_and_history_tool_calls(chat_completions_history_page) -> None:
    chat_completions_history_page.locator(".sidebar-item").first.click()
    chat_completions_history_page.wait_for_selector("#detail .section", timeout=5000)

    result = chat_completions_history_page.evaluate(
        """() => {
          const body = entries[0].request.body;
          const messages = getMessages(body);
          const assistantBlocks = Array.from(document.querySelectorAll('#detail .msg.assistant'))
            .map(el => el.innerText.trim());
          return {
            system: extractSystem(body),
            roles: messages.map(m => m.role),
            assistantContent: messages.find(m => m.role === 'assistant')?.content || [],
            titles: Array.from(document.querySelectorAll('#detail .section .title')).map(el => el.textContent),
            detailText: document.querySelector('#detail').innerText,
            assistantBlocks,
          };
        }"""
    )

    assert result["system"] == "Kimi CLI regression system prompt."
    assert result["roles"] == ["user", "assistant", "tool", "user"]
    assert result["assistantContent"] == [
        {"type": "tool_use", "id": "call_read", "name": "read_file", "input": {"path": "pyproject.toml"}}
    ]
    assert "System Prompt" in result["titles"]
    assert "Kimi CLI regression system prompt." in result["detailText"]
    assert "read_file" in result["detailText"]
    assert all(block != "ASSISTANT" for block in result["assistantBlocks"])


def test_viewer_treats_codex_forward_websocket_path_as_primary(responses_page) -> None:
    result = responses_page.evaluate(
        """() => ({
          tier: pathTier('/backend-api/codex/responses'),
          primary: isPathPrimary('/backend-api/codex/responses')
        })"""
    )

    assert result == {"tier": 0, "primary": True}


def test_viewer_sorts_dotted_websocket_turns_by_numeric_segments(responses_page) -> None:
    result = responses_page.evaluate(
        """() => {
          const makeEntry = (turn) => ({
            timestamp: '2026-05-07T10:00:00Z',
            request_id: 'req_' + turn,
            turn,
            duration_ms: 1,
            request: {
              method: 'POST',
              path: '/backend-api/codex/responses',
              body: { model: 'gpt-test' }
            },
            response: {
              status: 200,
              body: { usage: { input_tokens: 1, output_tokens: 1 }, output: [] }
            }
          });
          entries = [makeEntry('1.12'), makeEntry('1.2'), makeEntry(2)];
          activePaths = new Set(['/backend-api/codex/responses']);
          searchQuery = '';
          activeTools = null;
          applyFilter(false);
          return {
            filteredTurns: filtered.map(e => e.turn),
            sidebarTurns: [...document.querySelectorAll('.sidebar-item .si-turn')].map(el => el.textContent)
          };
        }"""
    )

    assert result == {
        "filteredTurns": ["1.2", "1.12", 2],
        "sidebarTurns": ["Turn 1.2", "Turn 1.12", "Turn 2"],
    }


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


def test_viewer_interleaves_codex_ws_tool_results_with_prior_outputs(responses_page) -> None:
    result = responses_page.evaluate(
        """() => {
          const record = {
            request_id: 'req_interleave',
            turn: 21,
            transport: 'websocket',
            request: {
              method: 'WEBSOCKET',
              path: '/backend-api/codex/responses',
              headers: {},
              body: {
                type: 'response.create',
                model: 'gpt-5.5',
                input: [],
                stream: true
              },
              ws_events: [
                {
                  type: 'response.create',
                  model: 'gpt-5.5',
                  previous_response_id: 'resp_prefetch',
                  input: [
                    {
                      type: 'message',
                      role: 'user',
                      content: [{ type: 'input_text', text: 'Run two tools.' }]
                    }
                  ],
                  tools: [{ type: 'function', name: 'exec_command' }],
                  stream: true
                },
                {
                  type: 'response.create',
                  model: 'gpt-5.5',
                  previous_response_id: 'resp_first',
                  input: [
                    { type: 'function_call_output', call_id: 'call_first', output: 'first result' }
                  ],
                  tools: [{ type: 'function', name: 'exec_command' }],
                  stream: true
                },
                {
                  type: 'response.create',
                  model: 'gpt-5.5',
                  previous_response_id: 'resp_second',
                  input: [
                    { type: 'custom_tool_call_output', call_id: 'call_second', output: 'second result' }
                  ],
                  tools: [{ type: 'function', name: 'exec_command' }],
                  stream: true
                }
              ]
            },
            response: {
              status: 101,
              headers: {},
              body: {},
              ws_events: [
                { type: 'response.created', response: { id: 'resp_first', status: 'in_progress', model: 'gpt-5.5' } },
                {
                  type: 'response.output_item.done',
                  output_index: 0,
                  item: {
                    id: 'msg_first',
                    type: 'message',
                    role: 'assistant',
                    content: [{ type: 'output_text', text: 'First decision' }]
                  }
                },
                {
                  type: 'response.output_item.done',
                  output_index: 1,
                  item: {
                    id: 'fc_first',
                    type: 'function_call',
                    name: 'exec_command',
                    call_id: 'call_first',
                    arguments: '{\"cmd\":\"pwd\"}'
                  }
                },
                {
                  type: 'response.completed',
                  response: {
                    id: 'resp_first',
                    status: 'completed',
                    model: 'gpt-5.5',
                    output: [],
                    usage: { input_tokens: 10, output_tokens: 4, total_tokens: 14 }
                  }
                },
                {
                  type: 'response.created',
                  response: {
                    id: 'resp_second',
                    status: 'in_progress',
                    model: 'gpt-5.5',
                    previous_response_id: 'resp_first'
                  }
                },
                {
                  type: 'response.output_item.done',
                  output_index: 0,
                  item: {
                    id: 'msg_second',
                    type: 'message',
                    role: 'assistant',
                    content: [{ type: 'output_text', text: 'Second decision' }]
                  }
                },
                {
                  type: 'response.output_item.done',
                  output_index: 1,
                  item: {
                    id: 'fc_second',
                    type: 'function_call',
                    name: 'exec_command',
                    call_id: 'call_second',
                    arguments: '{\"cmd\":\"ls\"}'
                  }
                },
                {
                  type: 'response.completed',
                  response: {
                    id: 'resp_second',
                    status: 'completed',
                    model: 'gpt-5.5',
                    previous_response_id: 'resp_first',
                    output: [],
                    usage: { input_tokens: 20, output_tokens: 4, total_tokens: 24 }
                  }
                },
                {
                  type: 'response.created',
                  response: {
                    id: 'resp_final',
                    status: 'in_progress',
                    model: 'gpt-5.5',
                    previous_response_id: 'resp_second'
                  }
                },
                {
                  type: 'response.output_item.done',
                  output_index: 0,
                  item: {
                    id: 'msg_final',
                    type: 'message',
                    role: 'assistant',
                    content: [{ type: 'output_text', text: 'Done' }]
                  }
                },
                {
                  type: 'response.completed',
                  response: {
                    id: 'resp_final',
                    status: 'completed',
                    model: 'gpt-5.5',
                    previous_response_id: 'resp_second',
                    output: [],
                    usage: { input_tokens: 30, output_tokens: 2, total_tokens: 32 }
                  }
                }
              ]
            }
          };
          const expanded = expandWebSocketResponseEntries([record]);
          const messages = getMessages(expanded[2].request.body);
          return messages.map(message => {
            const content = Array.isArray(message.content) ? message.content : [];
            const toolUse = content.find(block => block.type === 'tool_use');
            if (toolUse) return `assistant:tool_use:${toolUse.id}:${toolUse.name}`;
            const toolResult = content.find(block => block.type === 'tool_result');
            if (toolResult) return `tool:${toolResult.tool_use_id}:${toolResult.content}`;
            const text = content.map(block => block.text || '').filter(Boolean).join(' ');
            return `${message.role}:text:${text}`;
          });
        }"""
    )

    assert result == [
        "user:text:Run two tools.",
        "assistant:text:First decision",
        "assistant:tool_use:call_first:exec_command",
        "tool:call_first:first result",
        "assistant:text:Second decision",
        "assistant:tool_use:call_second:exec_command",
        "tool:call_second:second result",
    ]


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


def test_viewer_normalizes_generic_responses_tool_call_items(responses_page) -> None:
    result = responses_page.evaluate(
        """() => {
          const body = {
            input: [
              { type: 'message', role: 'user', content: [{ type: 'input_text', text: 'Search the docs.' }] },
              { type: 'web_search_call', status: 'completed', action: { type: 'search', query: 'Responses items' } },
              {
                type: 'computer_call_output',
                call_id: 'call_screen',
                output: { type: 'computer_screenshot', image_url: 'https://example.test/screen.png' }
              }
            ]
          };
          const output = normalizeResponseOutput([
            { type: 'web_search_call', status: 'completed', action: { type: 'search', query: 'Responses items' } },
            { type: 'file_search_call', status: 'completed', queries: ['viewer parser'] },
            { type: 'custom_tool_call', status: 'completed', name: 'deploy_preview' }
          ]);
          const messages = getMessages(body);
          return {
            responseNames: output.content.filter(block => block.type === 'tool_use').map(block => block.name),
            responseInputs: output.content.filter(block => block.type === 'tool_use').map(block => block.input),
            roles: messages.map(message => message.role),
            renderedMessages: renderMessages(messages)
          };
        }"""
    )

    assert result["responseNames"] == ["web_search", "file_search", "deploy_preview"]
    assert result["responseInputs"][0] == {"action": {"type": "search", "query": "Responses items"}}
    assert result["roles"] == ["user", "assistant", "tool"]
    assert "web_search" in result["renderedMessages"]
    assert "computer_screenshot" in result["renderedMessages"]


def test_viewer_renders_codex_tool_search_call_and_output(responses_page) -> None:
    result = responses_page.evaluate(
        """() => {
          const record = {
            request_id: 'req_tool_search',
            turn: 7,
            transport: 'websocket',
            request: {
              method: 'WEBSOCKET',
              path: '/backend-api/codex/responses',
              headers: {},
              body: {
                type: 'response.create',
                model: 'gpt-5.5',
                input: [],
                stream: true
              },
              ws_events: [
                {
                  type: 'response.create',
                  model: 'gpt-5.5',
                  input: [
                    {
                      type: 'message',
                      role: 'user',
                      content: [{ type: 'input_text', text: 'Find browser automation tools.' }]
                    }
                  ],
                  tools: [{ type: 'tool_search', description: '# Tool discovery' }],
                  stream: true
                },
                {
                  type: 'response.create',
                  model: 'gpt-5.5',
                  previous_response_id: 'resp_search',
                  input: [
                    {
                      type: 'tool_search_output',
                      call_id: 'call_search',
                      status: 'completed',
                      execution: 'client',
                      tools: [
                        {
                          type: 'namespace',
                          name: 'mcp__codex_apps__figma',
                          tools: [
                            { type: 'function', name: '_use_figma' },
                            { type: 'function', name: '_generate_figma_design' }
                          ]
                        }
                      ]
                    }
                  ],
                  tools: [
                    { type: 'tool_search', description: '# Tool discovery' },
                    { type: 'namespace', name: 'mcp__codex_apps__figma' }
                  ],
                  stream: true
                }
              ]
            },
            response: {
              status: 101,
              headers: {},
              body: {},
              ws_events: [
                { type: 'response.created', response: { id: 'resp_search', status: 'in_progress', model: 'gpt-5.5' } },
                {
                  type: 'response.output_item.done',
                  output_index: 0,
                  item: {
                    id: 'tsc_1',
                    type: 'tool_search_call',
                    status: 'completed',
                    arguments: { query: 'browser automation screenshot localhost plugin tool', limit: 5 },
                    call_id: 'call_search',
                    execution: 'client'
                  }
                },
                {
                  type: 'response.completed',
                  response: {
                    id: 'resp_search',
                    status: 'completed',
                    model: 'gpt-5.5',
                    output: [],
                    usage: { input_tokens: 10, output_tokens: 2, total_tokens: 12 }
                  }
                },
                {
                  type: 'response.created',
                  response: {
                    id: 'resp_final',
                    status: 'in_progress',
                    model: 'gpt-5.5',
                    previous_response_id: 'resp_search'
                  }
                },
                {
                  type: 'response.output_item.done',
                  output_index: 0,
                  item: {
                    id: 'msg_final',
                    type: 'message',
                    role: 'assistant',
                    content: [{ type: 'output_text', text: 'Use the Figma namespace.' }]
                  }
                },
                {
                  type: 'response.completed',
                  response: {
                    id: 'resp_final',
                    status: 'completed',
                    model: 'gpt-5.5',
                    previous_response_id: 'resp_search',
                    output: [],
                    usage: { input_tokens: 12, output_tokens: 4, total_tokens: 16 }
                  }
                }
              ]
            }
          };
          const expanded = expandWebSocketResponseEntries([record]);
          renderDetail(expanded[0]);
          const firstDetail = document.querySelector('#detail').innerText;
          renderDetail(expanded[1]);
          const secondDetail = document.querySelector('#detail').innerText;
          const responseToolNames = expanded.flatMap(e =>
            (getResponseOutput(e)?.content || [])
              .filter(block => block.type === 'tool_use')
              .map(block => block.name)
          );
          return {
            entryCount: expanded.length,
            firstDetail,
            secondDetail,
            secondRoles: getMessages(expanded[1].request.body).map(message => message.role),
            responseToolNames
          };
        }"""
    )

    assert result["entryCount"] == 2
    assert "tool_search" in result["firstDetail"]
    assert "browser automation screenshot localhost plugin tool" in result["firstDetail"]
    assert "limit" in result["firstDetail"]
    assert "tool_search_output" in result["secondDetail"]
    assert "mcp__codex_apps__figma" in result["secondDetail"]
    assert "mcp__codex_apps__figma._use_figma" in result["secondDetail"]
    assert result["secondRoles"] == ["user", "assistant", "tool"]
    assert result["responseToolNames"] == ["tool_search"]


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
