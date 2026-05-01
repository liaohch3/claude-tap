"""Viewer regression test: openclaw traces must not be mislabelled as Claude Code.

openclaw bundles skills similar in shape to Claude Code's, so its system prompt
can mention "Claude Code" deep inside an example list. The sidebar fingerprint
heuristic must prefer the explicit self-identification at the top of the prompt.
"""

from __future__ import annotations

import json

import pytest

pw_missing = False
try:
    from playwright.sync_api import sync_playwright  # noqa: F401
except ImportError:
    pw_missing = True


@pytest.mark.skipif(pw_missing, reason="playwright not installed")
def test_openclaw_prompt_is_labelled_openclaw_not_claude_code(tmp_path) -> None:
    from playwright.sync_api import sync_playwright

    from claude_tap.viewer import _generate_html_viewer

    # Mirror the real openclaw system prompt shape: opens with the
    # "You are openclaw" self-id, then mentions "Claude Code" deep inside
    # an example list. Without the self-id-first heuristic, the trace would
    # be mislabelled "Claude Code".
    system_prompt = (
        "You are openclaw, a personal AI assistant that helps users with "
        "software engineering tasks across any OS or platform.\n"
        "Use the instructions below and the tools available to you to assist the user.\n"
        + ("filler ... " * 200)
        + "\n(4) examples reference Claude Code, Cursor, or similar agent internals\n"
        + ("filler ... " * 200)
    )

    trace_path = tmp_path / "trace.jsonl"
    record = {
        "timestamp": "2026-05-02T10:00:00+00:00",
        "request_id": "req_1",
        "turn": 1,
        "duration_ms": 100,
        "request": {
            "method": "POST",
            "path": "/anthropic/v1/messages",
            "headers": {},
            "body": {
                "system": system_prompt,
                "messages": [{"role": "user", "content": [{"type": "text", "text": "ping"}]}],
            },
        },
        "response": {"status": 200, "headers": {}, "body": {"content": [], "usage": {}}},
    }
    trace_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    html_path = tmp_path / "trace.html"
    _generate_html_viewer(trace_path, html_path)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
        label = page.locator(".sidebar-item .si-task").first.inner_text()
        browser.close()

    assert label == "OpenClaw", f"openclaw trace mislabelled as {label!r}"
