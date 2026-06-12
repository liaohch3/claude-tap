"""Tests for Bedrock EventStream trace normalization in the HTML viewer."""

from __future__ import annotations

import base64
import json

import pytest

from claude_tap.viewer import _normalize_record_for_viewer

pw_missing = False
try:
    from playwright.sync_api import sync_playwright  # noqa: F401
except ImportError:
    pw_missing = True


def _bedrock_frame(payload: dict) -> str:
    encoded = base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    return "\x00\x00binary-prefix" + json.dumps({"bytes": encoded, "p": "abcdefghijk"}) + "\ufffd"


def _write_trace(trace_path, records: list[dict]) -> None:
    with trace_path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")


def test_normalize_record_for_viewer_decodes_bedrock_eventstream() -> None:
    body = "".join(
        [
            _bedrock_frame(
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg_1",
                        "type": "message",
                        "role": "assistant",
                        "model": "claude-opus-4-6",
                        "content": [],
                        "usage": {
                            "input_tokens": 3,
                            "cache_creation_input_tokens": 0,
                            "cache_read_input_tokens": 0,
                            "output_tokens": 0,
                        },
                    },
                }
            ),
            _bedrock_frame({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
            _bedrock_frame({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "OK"}}),
            _bedrock_frame({"type": "content_block_stop", "index": 0}),
            _bedrock_frame(
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": 1},
                }
            ),
            _bedrock_frame({"type": "message_stop", "amazon-bedrock-invocationMetrics": {"inputTokenCount": 3}}),
        ]
    )
    record = {
        "turn": 1,
        "request": {
            "method": "POST",
            "path": "/model/global.anthropic.claude-opus-4-6-v1/invoke-with-response-stream",
            "body": {"messages": [{"role": "user", "content": [{"type": "text", "text": "ping"}]}]},
        },
        "response": {"status": 200, "headers": {}, "body": body},
    }

    normalized = json.loads(_normalize_record_for_viewer(json.dumps(record)))

    assert [event["event"] for event in normalized["response"]["sse_events"]] == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]
    assert normalized["response"]["body"]["content"] == [{"type": "text", "text": "OK"}]
    assert normalized["response"]["body"]["usage"]["input_tokens"] == 3
    assert normalized["response"]["body"]["usage"]["output_tokens"] == 1


def test_normalize_record_for_viewer_decodes_bedrock_converse_stream() -> None:
    body = "".join(
        [
            _bedrock_frame({"messageStart": {"role": "assistant"}}),
            _bedrock_frame({"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "OK"}}}),
            _bedrock_frame({"contentBlockStop": {"contentBlockIndex": 0}}),
            _bedrock_frame({"messageStop": {"stopReason": "end_turn"}}),
            _bedrock_frame({"metadata": {"usage": {"inputTokens": 4, "outputTokens": 2, "totalTokens": 6}}}),
        ]
    )
    record = {
        "turn": 1,
        "request": {
            "method": "POST",
            "path": "/model/anthropic.claude-sonnet-4-20250514-v1:0/converse-stream",
            "body": {"messages": [{"role": "user", "content": [{"text": "ping"}]}]},
        },
        "response": {"status": 200, "headers": {}, "body": body},
    }

    normalized = json.loads(_normalize_record_for_viewer(json.dumps(record)))

    assert [event["event"] for event in normalized["response"]["sse_events"]] == [
        "message_start",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_delta",
    ]
    assert normalized["response"]["body"]["content"] == [{"type": "text", "text": "OK"}]
    assert normalized["response"]["body"]["stop_reason"] == "end_turn"
    assert normalized["response"]["body"]["usage"]["input_tokens"] == 4
    assert normalized["response"]["body"]["usage"]["output_tokens"] == 2
    assert normalized["response"]["body"]["usage"]["total_tokens"] == 6


def test_normalize_record_for_viewer_decodes_raw_bedrock_converse_stream_payloads() -> None:
    body = "".join(
        json.dumps(payload, separators=(",", ":"))
        for payload in (
            {"messageStart": {"role": "assistant"}},
            {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "OK"}}},
            {"metadata": {"usage": {"inputTokens": 4, "outputTokens": 2, "totalTokens": 6}}},
        )
    )
    record = {
        "turn": 1,
        "request": {
            "method": "POST",
            "path": "/model/anthropic.claude-sonnet-4-20250514-v1:0/converse-stream",
            "body": {"messages": [{"role": "user", "content": [{"text": "ping"}]}]},
        },
        "response": {"status": 200, "headers": {}, "body": body},
    }

    normalized = json.loads(_normalize_record_for_viewer(json.dumps(record)))

    assert normalized["response"]["body"]["content"] == [{"type": "text", "text": "OK"}]
    assert normalized["response"]["body"]["usage"]["input_tokens"] == 4
    assert normalized["response"]["body"]["usage"]["output_tokens"] == 2


def test_normalize_record_for_viewer_preserves_bedrock_reasoning_signature() -> None:
    body = "".join(
        [
            _bedrock_frame({"messageStart": {"role": "assistant"}}),
            _bedrock_frame(
                {
                    "contentBlockDelta": {
                        "contentBlockIndex": 0,
                        "delta": {"reasoningContent": {"text": "thinking", "signature": "sig-123"}},
                    }
                }
            ),
        ]
    )
    record = {
        "turn": 1,
        "request": {
            "method": "POST",
            "path": "/model/anthropic.claude-sonnet-4-20250514-v1:0/converse-stream",
            "body": {"messages": [{"role": "user", "content": [{"text": "ping"}]}]},
        },
        "response": {"status": 200, "headers": {}, "body": body},
    }

    normalized = json.loads(_normalize_record_for_viewer(json.dumps(record)))

    delta = normalized["response"]["sse_events"][1]["data"]["delta"]
    assert delta == {"type": "thinking_delta", "thinking": "thinking", "signature": "sig-123"}
    assert normalized["response"]["body"]["content"] == [
        {"type": "thinking", "thinking": "thinking", "signature": "sig-123"}
    ]


@pytest.mark.skipif(pw_missing, reason="playwright not installed")
def test_bedrock_invoke_path_is_primary_filter(tmp_path) -> None:
    from playwright.sync_api import sync_playwright

    from claude_tap.viewer import _generate_html_viewer

    bedrock_path = "/model/global.anthropic.claude-opus-4-6-v1/invoke-with-response-stream"
    paths = [
        bedrock_path,
        "/mcp-registry/v0/servers",
        "/inference-profiles",
        "/auxiliary/one",
    ]
    trace_path = tmp_path / "trace.jsonl"
    _write_trace(
        trace_path,
        [
            {
                "timestamp": f"2026-04-27T09:15:{turn:02d}+00:00",
                "request_id": f"req_{turn}",
                "turn": turn,
                "duration_ms": 100,
                "request": {
                    "method": "POST" if path == bedrock_path else "GET",
                    "path": path,
                    "headers": {},
                    "body": {
                        "messages": [{"role": "user", "content": [{"type": "text", "text": "ping"}]}],
                    }
                    if path == bedrock_path
                    else None,
                },
                "response": {"status": 200, "headers": {}, "body": {"content": [], "usage": {}}},
            }
            for turn, path in enumerate(paths, 1)
        ],
    )

    html_path = tmp_path / "trace.html"
    _generate_html_viewer(trace_path, html_path)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
        chip_text = page.locator("#path-filter .filter-chip").first.inner_text()
        sidebar_count = page.locator(".sidebar-item").count()
        more_text = page.locator("#path-filter .filter-chip-toggle").inner_text()
        browser.close()

    assert "invoke-with-response-stream" in chip_text
    assert sidebar_count == 1
    assert "+3" in more_text


@pytest.mark.skipif(pw_missing, reason="playwright not installed")
def test_bedrock_billing_header_does_not_become_task_label(tmp_path) -> None:
    from playwright.sync_api import sync_playwright

    from claude_tap.viewer import _generate_html_viewer

    trace_path = tmp_path / "trace.jsonl"
    _write_trace(
        trace_path,
        [
            {
                "timestamp": "2026-04-27T09:15:01+00:00",
                "request_id": "req_1",
                "turn": 1,
                "duration_ms": 100,
                "request": {
                    "method": "POST",
                    "path": "/model/global.anthropic.claude-haiku-4-5-20251001-v1:0/invoke-with-response-stream",
                    "headers": {},
                    "body": {
                        "system": [
                            {
                                "type": "text",
                                "text": (
                                    "x-anthropic-billing-header: cc_version=2.1.119.6a6; "
                                    "cc_entrypoint=sdk-ts-e2b-runner;\n"
                                    "You are a Claude agent, built on Anthropic's Claude Agent SDK."
                                ),
                            }
                        ],
                        "messages": [{"role": "user", "content": [{"type": "text", "text": "ping"}]}],
                    },
                },
                "response": {"status": 200, "headers": {}, "body": {"content": [], "usage": {}}},
            }
        ],
    )

    html_path = tmp_path / "trace.html"
    _generate_html_viewer(trace_path, html_path)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
        label = page.locator(".sidebar-item .si-task").first.inner_text()
        browser.close()

    assert label == "Claude Agent"


@pytest.mark.skipif(pw_missing, reason="playwright not installed")
def test_bedrock_converse_response_output_and_usage_render(tmp_path) -> None:
    from playwright.sync_api import sync_playwright

    from claude_tap.viewer import _generate_html_viewer

    trace_path = tmp_path / "trace.jsonl"
    _write_trace(
        trace_path,
        [
            {
                "timestamp": "2026-04-27T09:15:01+00:00",
                "request_id": "req_1",
                "turn": 1,
                "duration_ms": 100,
                "request": {
                    "method": "POST",
                    "path": "/model/anthropic.claude-sonnet-4-20250514-v1:0/converse",
                    "headers": {},
                    "body": {
                        "messages": [{"role": "user", "content": [{"text": "ping"}]}],
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
                                    {"text": "Bedrock says OK"},
                                    {
                                        "toolUse": {
                                            "toolUseId": "tool-1",
                                            "name": "lookup",
                                            "input": {"query": "ping"},
                                        }
                                    },
                                ],
                            }
                        },
                        "usage": {
                            "inputTokens": 9,
                            "outputTokens": 4,
                            "totalTokens": 13,
                            "cacheReadInputTokens": 3,
                            "cacheWriteInputTokens": 2,
                        },
                    },
                },
            }
        ],
    )

    html_path = tmp_path / "trace.html"
    _generate_html_viewer(trace_path, html_path)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
        result = page.evaluate(
            """
            () => ({
              output: getResponseOutput(entries[0]).content,
              usage: getUsage(entries[0]),
            })
            """
        )
        browser.close()

    assert result["output"] == [
        {"type": "text", "text": "Bedrock says OK"},
        {"type": "tool_use", "id": "tool-1", "name": "lookup", "input": {"query": "ping"}},
    ]
    assert result["usage"]["input_tokens"] == 9
    assert result["usage"]["output_tokens"] == 4
    assert result["usage"]["cache_read_input_tokens"] == 3
    assert result["usage"]["cache_creation_input_tokens"] == 2
