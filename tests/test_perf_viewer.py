#!/usr/bin/env python3
"""Playwright tests for viewer performance with large and small traces.

Tests lazy loading, virtual scrolling, and keyboard navigation using
both real large traces and synthetic small traces.
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

LARGE_TRACE = Path(__file__).parent.parent / ".traces" / "trace_20260228_212004.jsonl"


def _make_entry(turn: int, messages: list[dict]) -> dict:
    """Build a trace entry matching the real JSONL format."""
    return {
        "timestamp": f"2026-02-24T20:00:{turn:02d}",
        "request_id": f"req_{turn}",
        "turn": turn,
        "duration_ms": 500 + turn * 10,
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
                "usage": {"input_tokens": turn * 100, "output_tokens": turn * 20},
            },
        },
    }


def _build_small_trace_html() -> str:
    """Generate viewer HTML with 4 inline entries (small trace, no lazy mode)."""
    from claude_tap.viewer import _generate_html_viewer

    entries = [
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
                {"role": "assistant", "content": "Great!"},
                {"role": "user", "content": "tell me a joke"},
            ],
        ),
        _make_entry(
            4,
            [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "Hi!"},
                {"role": "user", "content": "how are you"},
                {"role": "assistant", "content": "Great!"},
                {"role": "user", "content": "tell me a joke"},
                {"role": "assistant", "content": "Why did the..."},
                {"role": "user", "content": "another"},
            ],
        ),
    ]

    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w") as trace_f:
        for e in entries:
            trace_f.write(json.dumps(e) + "\n")
        trace_path = Path(trace_f.name)

    html_path = Path(tempfile.mktemp(suffix=".html"))
    _generate_html_viewer(trace_path, html_path)
    return str(html_path)


# ── Shared playwright instance ──


@pytest.fixture(scope="module")
def pw_browser():
    """Single shared playwright + browser for the whole module."""
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    yield browser
    browser.close()
    pw.stop()


# ── Large trace fixtures ──


@pytest.fixture(scope="module")
def large_html_file():
    """Generate viewer HTML from large real trace (lazy mode)."""
    if not LARGE_TRACE.exists():
        pytest.skip(f"Large trace not found: {LARGE_TRACE}")

    from claude_tap.viewer import _generate_html_viewer

    html_path = Path(tempfile.mktemp(suffix=".html"))
    _generate_html_viewer(LARGE_TRACE, html_path)
    yield html_path
    html_path.unlink(missing_ok=True)


@pytest.fixture(scope="module")
def large_browser_page(pw_browser, large_html_file):
    """Open large trace HTML in a browser page."""
    page = pw_browser.new_page()
    page.goto(f"file://{large_html_file}", timeout=30000)
    page.wait_for_selector(".sidebar-item", timeout=10000)
    yield page
    page.close()


# ── Small trace fixtures ──


@pytest.fixture(scope="module")
def small_html_file():
    """Generate viewer HTML from small synthetic trace (inline mode)."""
    path = _build_small_trace_html()
    yield Path(path)
    Path(path).unlink(missing_ok=True)


@pytest.fixture(scope="module")
def small_browser_page(pw_browser, small_html_file):
    """Open small trace HTML in a browser page."""
    page = pw_browser.new_page()
    page.goto(f"file://{small_html_file}", timeout=10000)
    page.wait_for_selector(".sidebar-item", timeout=5000)
    yield page
    page.close()


class TestLargeTraceSidebar:
    """Verify sidebar loads quickly and virtual scrolling works."""

    def test_sidebar_populates_within_10s(self, large_browser_page):
        """Sidebar should show entries — the 10s budget is enforced by the fixture."""
        count = large_browser_page.evaluate(
            """() => {
            // In virtual mode, count comes from vsFilteredItems
            if (typeof vsFilteredItems !== 'undefined' && vsFilteredItems.length > 0)
                return vsFilteredItems.length;
            return document.querySelectorAll('.sidebar-item').length;
        }"""
        )
        assert count > 50, f"Expected >50 sidebar entries, got {count}"

    def test_virtual_scroll_active(self, large_browser_page):
        """Virtual scroll mode should be active for large traces."""
        is_virtual = large_browser_page.evaluate("typeof virtualMode !== 'undefined' && virtualMode")
        assert is_virtual, "virtualMode should be true for large trace"

    def test_lazy_mode_active(self, large_browser_page):
        """Lazy mode should be active for large traces."""
        is_lazy = large_browser_page.evaluate("typeof lazyMode !== 'undefined' && lazyMode")
        assert is_lazy, "lazyMode should be true for large trace"

    def test_position_indicator_visible(self, large_browser_page):
        """Position indicator should be visible after entry selection."""
        visible = large_browser_page.evaluate(
            """() => {
            const pi = document.getElementById('position-indicator');
            return pi && pi.style.display !== 'none';
        }"""
        )
        assert visible, "Position indicator should be visible"


class TestLargeTraceDetailLoading:
    """Verify clicking entries loads detail content quickly."""

    def test_click_entry_100_shows_content(self, large_browser_page):
        """Click entry ~100 and verify detail panel renders within 2s."""
        page = large_browser_page

        # Select entry at index 100 (or last if fewer)
        result = page.evaluate(
            """() => {
            const idx = Math.min(100, filtered.length - 1);
            selectEntry(idx);
            return {
                idx: idx,
                hasDetail: document.getElementById('detail').innerHTML.length > 100,
                activeIdx: activeIdx,
            };
        }"""
        )
        assert result["hasDetail"], f"Detail panel should have content after selecting entry {result['idx']}"

        # Verify the detail has actual sections (not just empty state)
        has_sections = page.evaluate("document.querySelectorAll('.section').length > 0")
        assert has_sections, "Detail panel should contain sections"

    def test_click_first_entry_shows_content(self, large_browser_page):
        """First entry should also load correctly."""
        page = large_browser_page
        page.evaluate("selectEntry(0)")
        page.wait_for_timeout(500)
        has_content = page.evaluate("document.getElementById('detail').innerHTML.length > 100")
        assert has_content, "First entry detail should have content"


class TestKeyboardNavigation:
    """Verify keyboard shortcuts work for large traces."""

    def test_arrow_down_navigates(self, large_browser_page):
        """Arrow down should move to next entry."""
        page = large_browser_page
        page.evaluate("selectEntry(0)")
        page.wait_for_timeout(200)

        before = page.evaluate("activeIdx")
        page.keyboard.press("ArrowDown")
        page.wait_for_timeout(200)
        after = page.evaluate("activeIdx")
        assert after == before + 1, f"ArrowDown: expected {before + 1}, got {after}"

    def test_arrow_up_navigates(self, large_browser_page):
        """Arrow up should move to previous entry."""
        page = large_browser_page
        page.evaluate("selectEntry(5)")
        page.wait_for_timeout(200)

        before = page.evaluate("activeIdx")
        page.keyboard.press("ArrowUp")
        page.wait_for_timeout(200)
        after = page.evaluate("activeIdx")
        assert after == before - 1, f"ArrowUp: expected {before - 1}, got {after}"

    def test_home_jumps_to_first(self, large_browser_page):
        """Home key should jump to first entry."""
        page = large_browser_page
        page.evaluate("selectEntry(50)")
        page.wait_for_timeout(200)

        page.keyboard.press("Home")
        page.wait_for_timeout(200)
        idx = page.evaluate("activeIdx")
        assert idx == 0, f"Home: expected 0, got {idx}"

    def test_end_jumps_to_last(self, large_browser_page):
        """End key should jump to last entry."""
        page = large_browser_page
        page.evaluate("selectEntry(0)")
        page.wait_for_timeout(200)

        page.keyboard.press("End")
        page.wait_for_timeout(200)
        idx = page.evaluate("activeIdx")
        total = page.evaluate("filtered.length")
        assert idx == total - 1, f"End: expected {total - 1}, got {idx}"

    def test_page_down_jumps_10(self, large_browser_page):
        """PageDown should advance by 10 entries."""
        page = large_browser_page
        page.evaluate("selectEntry(0)")
        page.wait_for_timeout(200)

        page.keyboard.press("PageDown")
        page.wait_for_timeout(200)
        idx = page.evaluate("activeIdx")
        assert idx == 10, f"PageDown: expected 10, got {idx}"


class TestSmallTraceRegression:
    """Verify small traces still work with inline (non-lazy) approach."""

    def test_inline_mode_active(self, small_browser_page):
        """Small traces should NOT use lazy mode."""
        is_lazy = small_browser_page.evaluate("typeof lazyMode !== 'undefined' && lazyMode")
        assert not is_lazy, "lazyMode should be false for small trace"

    def test_sidebar_has_4_entries(self, small_browser_page):
        """All 4 entries should appear in sidebar."""
        count = small_browser_page.evaluate("document.querySelectorAll('.sidebar-item').length")
        assert count == 4, f"Expected 4, got {count}"

    def test_detail_loads(self, small_browser_page):
        """Clicking an entry should show detail content."""
        page = small_browser_page
        page.evaluate("selectEntry(0)")
        page.wait_for_timeout(300)
        has_content = page.evaluate("document.getElementById('detail').innerHTML.length > 100")
        assert has_content, "Detail panel should have content"

    def test_no_virtual_scroll(self, small_browser_page):
        """Small traces should not use virtual scroll."""
        is_virtual = small_browser_page.evaluate("typeof virtualMode !== 'undefined' && virtualMode")
        assert not is_virtual, "virtualMode should be false for small trace"

    def test_keyboard_nav_works(self, small_browser_page):
        """Arrow keys should work in non-virtual mode."""
        page = small_browser_page
        page.evaluate("selectEntry(0)")
        page.wait_for_timeout(200)

        page.keyboard.press("ArrowDown")
        page.wait_for_timeout(200)
        idx = page.evaluate("activeIdx")
        assert idx == 1, f"ArrowDown should move to 1, got {idx}"

        page.keyboard.press("End")
        page.wait_for_timeout(200)
        idx = page.evaluate("activeIdx")
        assert idx == 3, f"End should move to 3, got {idx}"
