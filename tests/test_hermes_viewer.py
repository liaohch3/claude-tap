"""Playwright test: hermes traces should label as 'Hermes' even when the
system prompt mentions other agent brand names (Claude Code, OpenClaw, etc.)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

pw_missing = False
try:
    from playwright.sync_api import sync_playwright  # noqa: F401
except ImportError:
    pw_missing = True

pytestmark = pytest.mark.skipif(pw_missing, reason="playwright not installed")


HERMES_SOUL_WITH_BRAND_MENTIONS = (
    "You are Hermes Agent, an intelligent AI assistant created by Nous Research. "
    "You are helpful, knowledgeable, and direct. You assist users with a wide "
    "range of tasks. For comparison, you may have used Claude Code or OpenClaw "
    "in the past — those are different agents. You communicate clearly and "
    "prioritize being genuinely useful."
)


def _build_hermes_trace_html() -> Path:
    from claude_tap.viewer import _generate_html_viewer

    entry = {
        "timestamp": "2026-05-02T10:00:00",
        "request_id": "req_1",
        "turn": 1,
        "duration_ms": 500,
        "request": {
            "method": "POST",
            "path": "/v1/chat/completions",
            "headers": {},
            "body": {
                "model": "openai/gpt-4",
                "messages": [
                    {"role": "system", "content": HERMES_SOUL_WITH_BRAND_MENTIONS},
                    {"role": "user", "content": "hi"},
                ],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "description": "Read a project file.",
                            "parameters": {
                                "type": "object",
                                "properties": {"path": {"type": "string"}},
                                "required": ["path"],
                            },
                        },
                    },
                ],
            },
        },
        "response": {
            "status": 200,
            "body": {
                "choices": [{"message": {"role": "assistant", "content": "Hello!"}}],
                "model": "openai/gpt-4",
                "usage": {"prompt_tokens": 80, "completion_tokens": 5},
            },
        },
    }

    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w", encoding="utf-8") as trace_f:
        trace_f.write(json.dumps(entry) + "\n")
        trace_path = Path(trace_f.name)

    html_path = Path(tempfile.mktemp(suffix=".html"))
    _generate_html_viewer(trace_path, html_path)
    trace_path.unlink(missing_ok=True)
    return html_path


def _build_hermes_chat_continuation_trace_html() -> Path:
    """Build a realistic Hermes Chat Completions tool loop for Flow tests."""

    from claude_tap.viewer import _generate_html_viewer

    model = "openai/gpt-4"
    tools = [
        {
            "type": "function",
            "function": {
                "name": "list_files",
                "description": "List files in a directory.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a project file.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        },
    ]
    system = {"role": "system", "content": HERMES_SOUL_WITH_BRAND_MENTIONS}
    user = {"role": "user", "content": "Inspect the project repository."}
    list_call = {
        "id": "call_list_files",
        "type": "function",
        "function": {"name": "list_files", "arguments": '{"path":"."}'},
    }
    read_call = {
        "id": "call_read_file",
        "type": "function",
        "function": {"name": "read_file", "arguments": '{"path":"README.md"}'},
    }

    def make_record(
        turn: int,
        request_id: str,
        messages: list[dict[str, object]],
        response_message: dict[str, object],
        *,
        input_tokens: int,
        output_tokens: int,
    ) -> dict[str, object]:
        return {
            "timestamp": f"2026-05-02T10:00:0{turn}",
            "request_id": request_id,
            "turn": turn,
            "duration_ms": 100 + turn * 10,
            "request": {
                "method": "POST",
                "path": "/v1/chat/completions",
                "headers": {},
                "body": {
                    "model": model,
                    "messages": messages,
                    "tools": tools,
                },
            },
            "response": {
                "status": 200,
                "headers": {},
                "body": {
                    "choices": [{"message": response_message}],
                    "model": model,
                    "usage": {"prompt_tokens": input_tokens, "completion_tokens": output_tokens},
                },
            },
        }

    records = [
        make_record(
            1,
            "req_hermes_flow_1",
            [system, user],
            {"role": "assistant", "content": None, "tool_calls": [list_call]},
            input_tokens=100,
            output_tokens=8,
        ),
        make_record(
            2,
            "req_hermes_flow_2",
            [
                system,
                user,
                {"role": "assistant", "content": None, "tool_calls": [list_call]},
                {"role": "tool", "tool_call_id": "call_list_files", "name": "list_files", "content": "README.md\nsrc/"},
            ],
            {"role": "assistant", "content": None, "tool_calls": [read_call]},
            input_tokens=120,
            output_tokens=9,
        ),
        make_record(
            3,
            "req_hermes_flow_3",
            [
                system,
                user,
                {"role": "assistant", "content": None, "tool_calls": [list_call]},
                {"role": "tool", "tool_call_id": "call_list_files", "name": "list_files", "content": "README.md\nsrc/"},
                {"role": "assistant", "content": None, "tool_calls": [read_call]},
                {
                    "role": "tool",
                    "tool_call_id": "call_read_file",
                    "name": "read_file",
                    "content": "Project README contents",
                },
            ],
            {"role": "assistant", "content": "Repository inspected."},
            input_tokens=140,
            output_tokens=10,
        ),
    ]

    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w", encoding="utf-8") as trace_f:
        for record in records:
            trace_f.write(json.dumps(record) + "\n")
        trace_path = Path(trace_f.name)

    html_path = Path(tempfile.mktemp(suffix=".html"))
    _generate_html_viewer(trace_path, html_path)
    trace_path.unlink(missing_ok=True)
    return html_path


def test_hermes_trace_labels_as_hermes_not_claude_code() -> None:
    from playwright.sync_api import sync_playwright

    html_path = _build_hermes_trace_html()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"file://{html_path}", timeout=10000)
            page.wait_for_selector(".sidebar-item .si-task", timeout=5000)
            label = page.locator(".sidebar-item .si-task").first.text_content()
            assert label == "Hermes", (
                f"Expected sidebar label 'Hermes', got {label!r}. The hermes "
                f"system prompt mentions 'Claude Code' and 'OpenClaw' in passing — "
                f"the self-id phrase 'You are Hermes Agent' must win over those "
                f"generic substring matches."
            )
            default_tokens = page.locator(".default-token-card").all_inner_texts()
            assert default_tokens == ["Input\n80\ntok", "Output\n5\ntok"]
            browser.close()
    finally:
        html_path.unlink(missing_ok=True)


def test_hermes_trace_input_shows_ordered_modules_and_token_breakdown() -> None:
    from playwright.sync_api import sync_playwright

    html_path = _build_hermes_trace_html()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"file://{html_path}", timeout=10000)
            page.wait_for_selector(".sidebar-item", timeout=5000)
            page.locator('.detail-tab[data-tab="trace"]').click()
            page.wait_for_selector(".trace-input-module", timeout=5000)

            state = page.evaluate(
                """() => ({
                  total: document.querySelector('.trace-input-total')?.textContent.trim(),
                  labels: Array.from(document.querySelectorAll('.trace-input-label')).map(el => el.textContent),
                  indexes: Array.from(document.querySelectorAll('.trace-input-index')).map(el => el.textContent),
                  moduleTokens: Array.from(document.querySelectorAll('.trace-input-tokens')).map(el => Number(el.textContent.replace(/[^0-9]/g, ''))),
                  estimatedLabel: document.querySelector('.trace-input-block .trace-badge')?.textContent,
                  openLabels: Array.from(document.querySelectorAll('.trace-input-module[open] .trace-input-label')).map(el => el.textContent),
                  outputTotal: document.querySelector('.trace-output-total')?.textContent.trim(),
                  outputText: document.querySelector('.trace-output-content')?.textContent.trim(),
                  rawOutputOpen: document.querySelector('.trace-output-raw')?.open,
                  nativeCacheTotal: inputTokenTotal({
                    input_tokens: 12,
                    cache_read_input_tokens: 40,
                    cache_creation_input_tokens: 8,
                    _cache_read_in_input: false,
                  }),
                  embeddedCacheTotal: inputTokenTotal({
                    input_tokens: 80,
                    cache_read_input_tokens: 60,
                    _cache_read_in_input: true,
                  }),
                })"""
            )

            assert state["total"] == "80 tok"
            assert state["labels"] == ["System", "Tools", "User"]
            assert state["indexes"] == ["01", "02", "03"]
            assert sum(state["moduleTokens"]) == 80
            assert all(value > 0 for value in state["moduleTokens"])
            assert state["estimatedLabel"] == "Estimated split"
            assert state["openLabels"] == ["User"]
            assert state["outputTotal"] == "5 tok"
            assert state["outputText"] == "Hello!"
            assert state["rawOutputOpen"] is False
            assert state["nativeCacheTotal"] == 60
            assert state["embeddedCacheTotal"] == 80
            browser.close()
    finally:
        html_path.unlink(missing_ok=True)


def test_hermes_trace_output_shows_rate_limit_error_content() -> None:
    from playwright.sync_api import sync_playwright

    html_path = _build_hermes_trace_html()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"file://{html_path}", timeout=10000)
            page.wait_for_selector(".sidebar-item", timeout=5000)
            page.evaluate(
                """() => {
                  entries[0].response.status = 429;
                  entries[0].response.body = {
                    error: { message: 'rpm exhausted', type: 'quota_exceeded_error', code: '8' }
                  };
                  renderDetail(entries[0]);
                }"""
            )
            page.locator('.detail-tab[data-tab="trace"]').click()

            output_text = page.locator(".trace-output-content").inner_text()

            assert "HTTP 429" in output_text
            assert "rpm exhausted" in output_text
            browser.close()
    finally:
        html_path.unlink(missing_ok=True)


def test_hermes_chat_continuation_flow_groups_display_turns_and_lineage() -> None:
    from playwright.sync_api import sync_playwright

    html_path = _build_hermes_chat_continuation_trace_html()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"file://{html_path}", timeout=10000)
            page.wait_for_selector(".sidebar-item", timeout=5000)
            page.locator(".sidebar-item").first.click()
            page.wait_for_selector('.detail-tab[data-tab="default"].active', timeout=5000)
            page.locator('.detail-tab[data-tab="flow"]').click()
            page.wait_for_selector("#detail .flow-canvas", timeout=5000)

            state = page.evaluate(
                """() => ({
                  displayTurns: entries.map(entry => entry.display_turn),
                  turns: Array.from(document.querySelectorAll('#detail .flow-node-turn')).map(card => ({
                    turn: card.dataset.turn || '',
                    text: card.innerText || '',
                    inputSources: Array.from(card.querySelectorAll('.flow-input-module .flow-module-source')).map(el => el.innerText),
                    inputPreviews: Array.from(card.querySelectorAll('.flow-input-module .flow-module-preview')).map(el => el.innerText),
                  })),
                  tools: Array.from(document.querySelectorAll('#detail .flow-node-tool')).map(tool => tool.innerText || ''),
                })"""
            )
            browser.close()
    finally:
        html_path.unlink(missing_ok=True)

    assert state["displayTurns"] == ["1.1", "1.2", "1.3"]
    assert [turn["turn"] for turn in state["turns"]] == ["1.1", "1.2", "1.3"]
    assert any("From Turn 1.1" in source and "list_files" in source for source in state["turns"][1]["inputSources"])
    assert any("From Turn 1.2" in source and "read_file" in source for source in state["turns"][2]["inputSources"])
    assert "README.md" in state["turns"][1]["inputPreviews"][0]
    assert "Project README contents" in state["turns"][2]["inputPreviews"][0]
    assert all("call_" not in turn["text"] and '{"' not in turn["text"] for turn in state["turns"])
    assert "list_files" in state["tools"][0]
    assert "." in state["tools"][0]
    assert "call_" not in state["tools"][0]
    assert '{"' not in state["tools"][0]
    assert "read_file" in state["tools"][1]
    assert "README.md" in state["tools"][1]
    assert "call_" not in state["tools"][1]
    assert '{"' not in state["tools"][1]


def test_hermes_chat_continuation_flow_modules_reveal_tool_payloads() -> None:
    from playwright.sync_api import sync_playwright

    html_path = _build_hermes_chat_continuation_trace_html()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"file://{html_path}", timeout=10000)
            page.wait_for_selector(".sidebar-item", timeout=5000)
            page.locator(".sidebar-item").first.click()
            page.wait_for_selector('.detail-tab[data-tab="default"].active', timeout=5000)
            page.locator('.detail-tab[data-tab="flow"]').click()
            page.wait_for_selector("#detail .flow-canvas", timeout=5000)

            input_details: list[str] = []
            for turn, expected in (("1.2", "call_list_files"), ("1.3", "call_read_file")):
                module = page.locator(f"#detail .flow-node-turn[data-turn='{turn}'] .flow-input-module").first
                module.click()
                page.wait_for_function(
                    """expected => (document.querySelector('#detail .flow-details')?.innerText || '').includes(expected)""",
                    arg=expected,
                )
                input_details.append(page.locator("#detail .flow-details").inner_text())
            browser.close()
    finally:
        html_path.unlink(missing_ok=True)

    assert "call_list_files" in input_details[0]
    assert "README.md" in input_details[0]
    assert "call_read_file" in input_details[1]
    assert "Project README contents" in input_details[1]


def test_hermes_flow_groups_child_calls_under_delegate_and_keeps_lineage_details() -> None:
    """One child session may make several API calls but renders as one branch."""

    from playwright.sync_api import sync_playwright

    from claude_tap.viewer import _generate_html_viewer

    root_id = "hermes-root-flow"
    root_capture = {
        "hermes_root_session_id": root_id,
        "hermes_leaf_session_id": root_id,
        "hermes_root_turn": "1",
        "hermes_session_source": "cli",
        "hermes_session_resolution": "exact",
    }

    def make_record(
        turn: int,
        request_id: str,
        capture: dict[str, str],
        messages: list[dict[str, object]],
        message: dict[str, object],
    ) -> dict[str, object]:
        return {
            "timestamp": f"2026-05-02T11:00:0{turn}",
            "request_id": request_id,
            "turn": turn,
            "duration_ms": 100 + turn,
            "capture": capture,
            "request": {
                "method": "POST",
                "path": "/v1/chat/completions",
                "headers": {},
                "body": {"model": "openai/gpt-4", "messages": messages},
            },
            "response": {
                "status": 200,
                "headers": {},
                "body": {
                    "choices": [{"message": message}],
                    "usage": {"prompt_tokens": 20 + turn, "completion_tokens": 5},
                },
            },
        }

    delegate_call = {
        "id": "call_delegate_parser",
        "type": "function",
        "function": {
            "name": "delegate_task",
            "arguments": '{"delegation_id":"deleg-parser","goals":["Inspect parser tests","Report failures"]}',
        },
    }
    root_user = {"role": "user", "content": "Investigate parser failures."}
    root_assistant = {"role": "assistant", "content": None, "tool_calls": [delegate_call]}
    child_capture = {
        "hermes_root_session_id": root_id,
        "hermes_leaf_session_id": "hermes-leaf-parser",
        "hermes_parent_session_id": root_id,
        "hermes_root_turn": "1",
        "hermes_session_source": "subagent",
        "hermes_session_resolution": "exact",
    }
    second_root_capture = {**root_capture, "hermes_root_turn": "2"}
    second_child_capture = {**child_capture, "hermes_root_turn": "2", "hermes_leaf_session_id": "hermes-leaf-second"}
    records = (
        make_record(1, "root-delegate", root_capture, [root_user], root_assistant),
        make_record(
            2,
            "child-parser-1",
            child_capture,
            [{"role": "user", "content": "Inspect parser tests."}],
            {"role": "assistant", "content": "Found two failing cases."},
        ),
        make_record(
            3,
            "child-parser-2",
            child_capture,
            [{"role": "user", "content": "Inspect parser tests."}],
            {
                "role": "assistant",
                "content": "Failure details returned.",
                "reasoning_content": "Internal diagnostic reasoning.",
            },
        ),
        make_record(
            4,
            "root-return",
            root_capture,
            [
                root_user,
                root_assistant,
                {
                    "role": "tool",
                    "tool_call_id": "call_delegate_parser",
                    "name": "delegate_task",
                    "content": '{"delegation_id":"deleg-parser","goals":["Inspect parser tests"]}',
                },
            ],
            {
                "role": "assistant",
                "content": "Parser findings received (delegation ID: `deleg_parser`).",
            },
        ),
        make_record(
            5,
            "root-second-turn",
            second_root_capture,
            [{"role": "user", "content": "Review deployment notes."}],
            {"role": "assistant", "content": "Deployment notes reviewed."},
        ),
        make_record(
            6,
            "child-second-turn",
            second_child_capture,
            [{"role": "user", "content": "Review deployment notes."}],
            {"role": "assistant", "content": "Deployment result."},
        ),
    )

    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w", encoding="utf-8") as trace_file:
        for record in records:
            trace_file.write(json.dumps(record) + "\n")
        trace_path = Path(trace_file.name)
    html_path = Path(tempfile.mktemp(suffix=".html"))
    _generate_html_viewer(trace_path, html_path)
    trace_path.unlink(missing_ok=True)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"file://{html_path}", timeout=10000)
            page.wait_for_selector(".sidebar-item", timeout=5000)
            page.locator(".sidebar-item").first.click()
            page.locator('.detail-tab[data-tab="flow"]').click()
            page.wait_for_selector("#detail .flow-canvas", timeout=5000)
            state = page.evaluate(
                """() => ({
                  turn: activeFlowGraph.turn,
                  agents: Array.from(document.querySelectorAll('#detail .flow-agent-node')).map(node => node.innerText),
                  branchClass: document.querySelector('#detail .flow-agent-branches')?.className || '',
                  graphAgents: activeFlowGraph.agents.map(agent => ({
                    id: agent.id,
                    leaf: agent.lineage.leafSessionId,
                    inputTokens: agent.inputTokens,
                    outputTokens: agent.outputTokens,
                  })),
                })"""
            )
            assert len(state["agents"]) == 1
            assert state["turn"] == "1"
            assert "Inspect parser tests" in state["agents"][0]
            assert "Failure details returned." in state["agents"][0]
            assert "Internal diagnostic reasoning" not in state["agents"][0]
            assert "deleg-parser" not in state["agents"][0]
            assert "deleg_parser" not in page.locator("#detail .flow-canvas").inner_text()
            assert page.locator("#detail .flow-main-lane-label").first.inner_text() == "Parent Agent"
            assert "parallel" not in state["branchClass"]
            assert len(state["graphAgents"]) == 1
            assert state["graphAgents"][0]["leaf"] == "hermes-leaf-parser"
            page.locator("#detail .flow-agent-node").first.click()
            details = page.locator("#detail .flow-details").inner_text()
            assert "hermes-root-flow" in details
            assert "hermes-leaf-parser" in details
            assert "hermes_parent_session_id" in details
            assert "deleg-parser" in details
            browser.close()
    finally:
        html_path.unlink(missing_ok=True)


def test_hermes_flow_matches_batch_goals_to_nearest_prior_delegate() -> None:
    """Batch goals map to their child summaries; a later batch is not reused."""

    from playwright.sync_api import sync_playwright

    from claude_tap.viewer import _generate_html_viewer

    root_capture = {
        "hermes_root_session_id": "batch-root",
        "hermes_leaf_session_id": "batch-root",
        "hermes_root_turn": "1",
        "hermes_session_source": "cli",
        "hermes_session_resolution": "exact",
    }

    def child_capture(leaf: str) -> dict[str, str]:
        return {
            "hermes_root_session_id": "batch-root",
            "hermes_leaf_session_id": leaf,
            "hermes_parent_session_id": "batch-root",
            "hermes_root_turn": "1",
            "hermes_session_source": "subagent",
            "hermes_session_resolution": "exact",
        }

    def record(
        turn: int, request_id: str, capture: dict[str, str], user: str, message: dict[str, object]
    ) -> dict[str, object]:
        return {
            "turn": turn,
            "request_id": request_id,
            "duration_ms": 10,
            "capture": capture,
            "request": {
                "path": "/v1/chat/completions",
                "body": {"model": "hermes", "messages": [{"role": "user", "content": user}]},
            },
            "response": {
                "status": 200,
                "body": {"choices": [{"message": message}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
            },
        }

    def delegate(goals: list[str]) -> dict[str, object]:
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "delegate",
                    "type": "function",
                    "function": {"name": "delegate_task", "arguments": json.dumps({"tasks": goals})},
                }
            ],
        }

    records = [
        record(1, "root-batch", root_capture, "parent", delegate(["alpha", "beta"])),
        record(
            2, "child-alpha", child_capture("leaf-alpha"), "alpha", {"role": "assistant", "content": "alpha output"}
        ),
        record(3, "child-beta", child_capture("leaf-beta"), "beta", {"role": "assistant", "content": "beta output"}),
        record(4, "root-next", root_capture, "parent", delegate(["gamma"])),
        record(
            5, "child-gamma", child_capture("leaf-gamma"), "gamma", {"role": "assistant", "content": "gamma output"}
        ),
    ]
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w", encoding="utf-8") as trace_file:
        trace_file.write("")
        trace_path = Path(trace_file.name)
    html_path = Path(tempfile.mktemp(suffix=".html"))
    _generate_html_viewer(trace_path, html_path)
    trace_path.unlink(missing_ok=True)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"file://{html_path}", timeout=10000)
            state = page.evaluate(
                "records => buildFlowGraph(records).agents.map(agent => ({summary: agent.summary, output: agent.outputSummary}))",
                records,
            )
            browser.close()
    finally:
        html_path.unlink(missing_ok=True)

    assert len(state) == 3
    assert [agent["summary"] for agent in state] == ["alpha", "beta", "gamma"]
    assert [agent["output"] for agent in state] == ["alpha output", "beta output", "gamma output"]
