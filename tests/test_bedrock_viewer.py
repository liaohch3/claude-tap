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
        "/auxiliary/two",
        "/auxiliary/three",
    ]
    trace_path = tmp_path / "trace.jsonl"
    with trace_path.open("w", encoding="utf-8") as f:
        for turn, path in enumerate(paths, 1):
            record = {
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
            f.write(json.dumps(record) + "\n")

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
    assert "+5" in more_text
