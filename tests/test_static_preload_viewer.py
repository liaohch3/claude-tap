"""Coverage for server-rendered trace content in generated viewer HTML."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_tap.viewer import _generate_html_viewer

pw_missing = False
try:
    from playwright.sync_api import sync_playwright  # noqa: F401
except ImportError:
    pw_missing = True


def _write_trace(tmp_path: Path) -> tuple[Path, Path]:
    trace_path = tmp_path / "trace.jsonl"
    html_path = tmp_path / "trace.html"
    record = {
        "timestamp": "2026-05-12T10:00:00+00:00",
        "request_id": "req_static_1",
        "turn": 1,
        "duration_ms": 1234,
        "request": {
            "method": "POST",
            "path": "/v1/responses",
            "body": {
                "model": "gpt-5.5",
                "instructions": "You are Codex.",
                "input": [
                    {"role": "user", "content": [{"type": "input_text", "text": "hello from static trace"}]},
                    {
                        "type": "function_call",
                        "name": "exec_command",
                        "arguments": '{"cmd":"gh issue list"}',
                    },
                    {"type": "function_call_output", "output": "issue list output"},
                ],
                "tools": [
                    {
                        "type": "function",
                        "name": "exec_command",
                        "description": "Run a shell command.",
                        "parameters": {
                            "type": "object",
                            "properties": {"cmd": {"type": "string", "description": "Command to run."}},
                            "required": ["cmd"],
                        },
                    }
                ],
            },
        },
        "response": {
            "status": 200,
            "body": {
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "hello from assistant"}]}],
                "usage": {
                    "input_tokens": 100,
                    "input_tokens_details": {"cached_tokens": 40},
                    "output_tokens": 12,
                },
            },
        },
    }
    trace_path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    _generate_html_viewer(trace_path, html_path)
    return trace_path, html_path


def test_generated_html_contains_static_trace_dom(tmp_path: Path) -> None:
    _, html_path = _write_trace(tmp_path)
    html = html_path.read_text(encoding="utf-8")

    assert 'id="drop-zone" style="display:none"' in html
    assert 'data-static-preload="1"' in html
    assert '<div class="sidebar-item active"' in html
    assert "hello from static trace" in html
    assert "hello from assistant" in html
    assert "exec_command" in html
    assert '<span class="stat-val" id="stat-tokens">112</span>' in html


@pytest.mark.skipif(pw_missing, reason="playwright not installed")
def test_generated_html_is_readable_without_javascript(tmp_path: Path) -> None:
    _, html_path = _write_trace(tmp_path)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(java_script_enabled=False)
        page = context.new_page()
        page.goto(html_path.resolve().as_uri(), wait_until="domcontentloaded", timeout=10000)

        assert page.locator("#drop-zone").is_hidden()
        assert page.locator(".sidebar-item").count() == 1
        assert page.locator("#detail").is_visible()
        assert page.locator("#detail").get_by_text("hello from static trace").count() > 0
        assert page.locator("#detail").get_by_text("hello from assistant").count() > 0
        assert page.locator("#detail").get_by_text("exec_command").count() > 0

        context.close()
        browser.close()
