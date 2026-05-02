#!/usr/bin/env python3
"""Browser integration test for diff nav button fix using Playwright.

Generates a self-contained HTML viewer with synthetic trace data,
opens it in a headless browser, and verifies that the ◀/▶ nav buttons
are correctly enabled/disabled after the .idx bug fix.
"""

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

# ── Synthetic trace data: 4-turn conversation chain ──
# Turn 1 → Turn 2 → Turn 3 → Turn 4
# This gives us 3 diff pairs to navigate between.


def _make_entry(turn: int, messages: list[dict]) -> dict:
    """Build a trace entry matching the real JSONL format."""
    return {
        "timestamp": f"2026-02-24T20:00:0{turn}",
        "request_id": f"req_{turn}",
        "turn": turn,
        "duration_ms": 500,
        "request": {
            "method": "POST",
            "path": "/v1/messages",
            "headers": {},
            "body": {
                "model": "claude-opus-4-6",
                "system": [{"type": "text", "text": "You are Claude"}],
                "messages": messages,
            },
        },
        "response": {
            "status": 200,
            "body": {
                "content": [{"type": "text", "text": f"Response for turn {turn}"}],
                "model": "claude-opus-4-6",
                "usage": {"input_tokens": turn * 10, "output_tokens": 5},
            },
        },
    }


TRACE_ENTRIES = [
    _make_entry(1, [{"role": "user", "content": "hello"}]),
    _make_entry(
        2,
        [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "Hi!"},
            {"role": "user", "content": "how are you"},
        ],
    ),
    _make_entry(
        3,
        [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "Hi!"},
            {"role": "user", "content": "how are you"},
            {"role": "assistant", "content": "I'm fine!"},
            {"role": "user", "content": "tell me a joke"},
        ],
    ),
    _make_entry(
        4,
        [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "Hi!"},
            {"role": "user", "content": "how are you"},
            {"role": "assistant", "content": "I'm fine!"},
            {"role": "user", "content": "tell me a joke"},
            {"role": "assistant", "content": "Why did the chicken..."},
            {"role": "user", "content": "another one"},
        ],
    ),
]


def _build_test_html() -> str:
    """Generate self-contained viewer HTML with embedded test trace data."""
    template_path = Path(__file__).parent.parent / "claude_tap" / "viewer.html"
    html = template_path.read_text(encoding="utf-8")

    records = [json.dumps(e) for e in TRACE_ENTRIES]
    data_js = (
        "const EMBEDDED_TRACE_DATA = [\n" + ",\n".join(records) + "\n];\n"
        'const __TRACE_JSONL_PATH__ = "/tmp/test.jsonl";\n'
        'const __TRACE_HTML_PATH__ = "/tmp/test.html";\n'
    )
    html = html.replace(
        "<script>\nconst $ = s =>",
        f"<script>\n{data_js}</script>\n<script>\nconst $ = s =>",
        1,
    )
    return html


@pytest.fixture(scope="module")
def html_file():
    """Write test HTML to a temp file."""
    html = _build_test_html()
    with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8") as f:
        f.write(html)
        return Path(f.name)


@pytest.fixture(scope="module")
def browser_page(html_file):
    """Launch headless Chromium and open the test HTML."""
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto(f"file://{html_file}")
    page.wait_for_selector(".sidebar-item", timeout=5000)
    yield page
    browser.close()
    pw.stop()


class TestDiffNavInBrowser:
    """Test diff navigation buttons in an actual browser."""

    def _open_diff_for_entry(self, page, entry_index: int):
        """Click the diff button on the Nth entry (0-indexed in filtered list)."""
        # Close any existing diff overlay first
        page.evaluate("document.querySelector('.diff-overlay')?.remove()")
        # Click the sidebar item to select it
        items = page.query_selector_all(".sidebar-item")
        items[entry_index].click()
        page.wait_for_timeout(200)
        # Click the diff button (showDiff) in the action bar
        page.evaluate("document.querySelector('.act-btn:nth-child(3)').click()")
        page.wait_for_selector(".diff-overlay", timeout=3000)

    def _get_nav_state(self, page) -> dict:
        """Get the current state of the diff nav buttons."""
        return page.evaluate("""() => {
            const overlay = document.querySelector('.diff-overlay');
            if (!overlay) return { overlayExists: false };
            const prev = overlay.querySelector('.diff-nav-prev');
            const next = overlay.querySelector('.diff-nav-next');
            const title = overlay.querySelector('.diff-title')?.textContent || '';
            return {
                overlayExists: true,
                prevDisabled: prev?.disabled ?? null,
                nextDisabled: next?.disabled ?? null,
                title: title,
            };
        }""")

    def test_entry_count(self, browser_page):
        """Verify all 4 entries loaded."""
        count = browser_page.evaluate("document.querySelectorAll('.sidebar-item').length")
        assert count == 4, f"Expected 4 entries, got {count}"

    def test_first_diff_pair_nav_state(self, browser_page):
        """At diff Turn1→Turn2 (first pair): prev disabled, next enabled."""
        self._open_diff_for_entry(browser_page, 1)
        state = self._get_nav_state(browser_page)
        assert state["overlayExists"], "Diff overlay should be open"
        assert state["prevDisabled"] is True, (
            f"BUG if False: prev should be disabled at first diff pair. State: {state}"
        )
        assert state["nextDisabled"] is False, (
            f"BUG if True: next should be enabled (can go to Turn2→Turn3). State: {state}"
        )

    def test_middle_diff_pair_nav_state(self, browser_page):
        """At diff Turn2→Turn3 (middle pair): both enabled."""
        self._open_diff_for_entry(browser_page, 2)
        state = self._get_nav_state(browser_page)
        assert state["overlayExists"], "Diff overlay should be open"
        assert state["prevDisabled"] is False, f"prev should be enabled (can go back to Turn1→Turn2). State: {state}"
        assert state["nextDisabled"] is False, f"next should be enabled (can go to Turn3→Turn4). State: {state}"

    def test_last_diff_pair_nav_state(self, browser_page):
        """At diff Turn3→Turn4 (last pair): prev enabled, next disabled."""
        self._open_diff_for_entry(browser_page, 3)
        state = self._get_nav_state(browser_page)
        assert state["overlayExists"], "Diff overlay should be open"
        assert state["prevDisabled"] is False, f"prev should be enabled (can go back to Turn2→Turn3). State: {state}"
        assert state["nextDisabled"] is True, f"next should be disabled at last diff pair. State: {state}"

    def test_click_right_navigates(self, browser_page):
        """Clicking ▶ at Turn1→Turn2 should navigate to Turn2→Turn3."""
        self._open_diff_for_entry(browser_page, 1)
        state_before = self._get_nav_state(browser_page)
        assert "1" in state_before["title"] and "2" in state_before["title"]

        # Click right arrow
        browser_page.click(".diff-nav-next")
        browser_page.wait_for_timeout(300)

        state_after = self._get_nav_state(browser_page)
        assert state_after["overlayExists"], "Overlay should still exist after clicking ▶"
        assert "2" in state_after["title"] and "3" in state_after["title"], (
            f"Should navigate to Turn2→Turn3, got title: {state_after['title']}"
        )

    def test_click_left_at_first_does_not_close(self, browser_page):
        """Clicking ◀ at Turn1→Turn2 should NOT close the overlay (regression test)."""
        self._open_diff_for_entry(browser_page, 1)
        state = self._get_nav_state(browser_page)
        assert state["prevDisabled"] is True, "prev should be disabled"

        # Even if we try to click the disabled button, overlay should remain
        browser_page.evaluate("""
            document.querySelector('.diff-nav-prev').click();
        """)
        browser_page.wait_for_timeout(300)

        state_after = self._get_nav_state(browser_page)
        assert state_after["overlayExists"], "BUG: overlay disappeared after clicking disabled ◀ at first diff pair!"

    def test_click_left_navigates_back(self, browser_page):
        """Clicking ◀ at Turn2→Turn3 should navigate back to Turn1→Turn2."""
        self._open_diff_for_entry(browser_page, 2)
        state_before = self._get_nav_state(browser_page)
        assert "2" in state_before["title"] and "3" in state_before["title"]

        # Click left arrow
        browser_page.click(".diff-nav-prev")
        browser_page.wait_for_timeout(300)

        state_after = self._get_nav_state(browser_page)
        assert state_after["overlayExists"], "Overlay should still exist after clicking ◀"
        assert "1" in state_after["title"] and "2" in state_after["title"], (
            f"Should navigate to Turn1→Turn2, got title: {state_after['title']}"
        )

    def test_keyboard_right_arrow(self, browser_page):
        """Pressing → key at Turn1→Turn2 should navigate to Turn2→Turn3."""
        self._open_diff_for_entry(browser_page, 1)

        browser_page.keyboard.press("ArrowRight")
        browser_page.wait_for_timeout(300)

        state = self._get_nav_state(browser_page)
        assert state["overlayExists"], "Overlay should exist after pressing →"
        assert "2" in state["title"] and "3" in state["title"], (
            f"→ should navigate to Turn2→Turn3, got: {state['title']}"
        )

    def test_keyboard_left_at_first_keeps_overlay(self, browser_page):
        """Pressing ← at first diff should NOT close overlay (regression)."""
        self._open_diff_for_entry(browser_page, 1)

        browser_page.keyboard.press("ArrowLeft")
        browser_page.wait_for_timeout(300)

        state = self._get_nav_state(browser_page)
        assert state["overlayExists"], "BUG: overlay disappeared after pressing ← at first diff!"

    def test_full_chain_traversal_right(self, browser_page):
        """Navigate the full chain: Turn1→2, then →Turn2→3, then →Turn3→4."""
        self._open_diff_for_entry(browser_page, 1)

        # At Turn1→Turn2
        s = self._get_nav_state(browser_page)
        assert "1" in s["title"] and "2" in s["title"]

        # → to Turn2→Turn3
        browser_page.click(".diff-nav-next")
        browser_page.wait_for_timeout(300)
        s = self._get_nav_state(browser_page)
        assert "2" in s["title"] and "3" in s["title"], f"Got: {s['title']}"

        # → to Turn3→Turn4
        browser_page.click(".diff-nav-next")
        browser_page.wait_for_timeout(300)
        s = self._get_nav_state(browser_page)
        assert "3" in s["title"] and "4" in s["title"], f"Got: {s['title']}"

        # Next should now be disabled
        assert s["nextDisabled"] is True, "Should be at end of chain"

    def test_full_chain_traversal_left(self, browser_page):
        """Navigate backward: start at Turn3→4, then ← all the way."""
        self._open_diff_for_entry(browser_page, 3)

        # At Turn3→Turn4
        s = self._get_nav_state(browser_page)
        assert "3" in s["title"] and "4" in s["title"]

        # ← to Turn2→Turn3
        browser_page.click(".diff-nav-prev")
        browser_page.wait_for_timeout(300)
        s = self._get_nav_state(browser_page)
        assert "2" in s["title"] and "3" in s["title"], f"Got: {s['title']}"

        # ← to Turn1→Turn2
        browser_page.click(".diff-nav-prev")
        browser_page.wait_for_timeout(300)
        s = self._get_nav_state(browser_page)
        assert "1" in s["title"] and "2" in s["title"], f"Got: {s['title']}"

        # Prev should now be disabled
        assert s["prevDisabled"] is True, "Should be at start of chain"
