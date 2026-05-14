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
            browser.close()
    finally:
        html_path.unlink(missing_ok=True)
