#!/usr/bin/env python3
"""Playwright browser tests for global trace search in viewer.html using real trace data."""

from __future__ import annotations

import json
import re
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

_WORD_RE = re.compile(r"[A-Za-z]{4,}")
_STOPWORDS = {
    "this",
    "that",
    "with",
    "from",
    "have",
    "will",
    "into",
    "your",
    "http",
    "https",
    "json",
    "true",
    "false",
    "null",
}


def _load_entries(trace_file: Path) -> list[dict]:
    lines = trace_file.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _pick_real_trace_file() -> Path:
    traces_dir = Path(__file__).parent.parent / ".traces"
    trace_files = sorted(traces_dir.glob("trace_*.jsonl"), key=lambda p: p.stat().st_size)
    candidates = []
    for path in trace_files:
        if path.stat().st_size == 0:
            continue
        line_count = sum(1 for _ in path.open("r", encoding="utf-8"))
        if line_count >= 4:
            candidates.append(path)
    if not candidates:
        raise RuntimeError("No real trace file with >=4 entries found in .traces/")
    return candidates[0]


def _extract_messages(body: dict | None) -> list[str]:
    if not body:
        return []
    texts: list[str] = []
    if isinstance(body.get("messages"), list):
        for msg in body["messages"]:
            content = msg.get("content")
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and isinstance(block.get("text"), str):
                        texts.append(block["text"])
    if isinstance(body.get("input"), list):
        for item in body["input"]:
            if item.get("type") != "message":
                continue
            content = item.get("content")
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and isinstance(block.get("text"), str):
                        texts.append(block["text"])
    return texts


def _pick_message_term(entries: list[dict]) -> tuple[str, int]:
    for idx, entry in enumerate(entries):
        texts = _extract_messages(entry.get("request", {}).get("body", {}))
        for text in texts:
            for word in _WORD_RE.findall(text):
                lw = word.lower()
                if lw not in _STOPWORDS:
                    return lw, idx
    return "model", 0


def _pick_cross_entry_term(entries: list[dict]) -> str:
    entry_texts = [json.dumps(entry, ensure_ascii=False).lower() for entry in entries]
    by_entry_count: dict[str, int] = {}
    total_counts: dict[str, int] = {}

    for text in entry_texts:
        seen = set()
        for word in _WORD_RE.findall(text):
            lw = word.lower()
            if lw in _STOPWORDS:
                continue
            total_counts[lw] = total_counts.get(lw, 0) + text.count(lw)
            seen.add(lw)
        for word in seen:
            by_entry_count[word] = by_entry_count.get(word, 0) + 1

    scored: list[tuple[int, int, str]] = []
    total_entries = len(entries)
    for word, entry_hits in by_entry_count.items():
        total_hits = total_counts.get(word, 0)
        if entry_hits >= 2 and total_hits <= 40 and entry_hits < total_entries:
            scored.append((entry_hits, total_hits, word))
    if scored:
        scored.sort()
        return scored[0][2]

    # Fallback: broad but always present in responses traces.
    return "response"


@pytest.fixture(scope="module")
def trace_entries() -> tuple[Path, list[dict], str, tuple[str, int]]:
    trace_file = _pick_real_trace_file()
    entries = _load_entries(trace_file)
    cross_term = _pick_cross_entry_term(entries)
    message_term = _pick_message_term(entries)
    return trace_file, entries, cross_term, message_term


@pytest.fixture(scope="module")
def html_file(trace_entries) -> Path:
    trace_file, _, _, _ = trace_entries
    with tempfile.TemporaryDirectory() as tmpdir:
        html_path = Path(tmpdir) / "search_test_viewer.html"
        _generate_html_viewer(trace_file, html_path)
        html = html_path.read_text(encoding="utf-8")
        # Persist file after tempdir exits for module-scoped browser fixture.
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8") as f:
            f.write(html)
            return Path(f.name)


@pytest.fixture(scope="module")
def browser_page(html_file):
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto(f"file://{html_file}")
    page.wait_for_selector(".sidebar-item", timeout=10000)
    yield page
    browser.close()
    pw.stop()


def _dispatch_find_shortcut(page, *, meta: bool, ctrl: bool) -> None:
    page.evaluate(
        """([metaKey, ctrlKey]) => {
            document.dispatchEvent(
                new KeyboardEvent('keydown', {
                    key: 'f',
                    metaKey,
                    ctrlKey,
                    bubbles: true,
                    cancelable: true,
                })
            );
        }""",
        [meta, ctrl],
    )


class TestViewerGlobalSearch:
    def test_cmd_or_ctrl_f_opens_custom_search(self, browser_page):
        _dispatch_find_shortcut(browser_page, meta=True, ctrl=False)
        browser_page.wait_for_selector("#global-search-overlay.open", timeout=3000)
        assert browser_page.evaluate("document.activeElement?.id") == "global-search-input"

        browser_page.keyboard.press("Escape")
        browser_page.wait_for_function(
            "() => !document.querySelector('#global-search-overlay')?.classList.contains('open')"
        )

        _dispatch_find_shortcut(browser_page, meta=False, ctrl=True)
        browser_page.wait_for_selector("#global-search-overlay.open", timeout=3000)
        assert browser_page.evaluate("document.activeElement?.id") == "global-search-input"

    def test_typing_highlights_and_match_counter(self, browser_page, trace_entries):
        _, _, cross_term, _ = trace_entries
        browser_page.fill("#global-search-input", cross_term)
        browser_page.wait_for_function("() => document.querySelectorAll('mark.global-search-hit').length > 0")
        count_text = browser_page.inner_text("#global-search-count")
        assert " of " in count_text
        assert "matches" in count_text

    def test_enter_navigates_matches(self, browser_page):
        before = browser_page.inner_text("#global-search-count")
        browser_page.keyboard.press("Enter")
        browser_page.wait_for_timeout(150)
        after = browser_page.inner_text("#global-search-count")
        assert before != after, f"Expected current match index to advance, got: {after}"

    def test_cross_entry_navigation_switches_sidebar(self, browser_page):
        start_turn = browser_page.inner_text(".sidebar-item.active .si-turn")
        switched = False
        for _ in range(80):
            browser_page.keyboard.press("Enter")
            browser_page.wait_for_timeout(80)
            now_turn = browser_page.inner_text(".sidebar-item.active .si-turn")
            if now_turn != start_turn:
                switched = True
                break
        assert switched, "Expected search navigation to jump to a different sidebar entry"

    def test_escape_closes_and_clears_highlights(self, browser_page):
        browser_page.keyboard.press("Escape")
        browser_page.wait_for_function(
            "() => !document.querySelector('#global-search-overlay')?.classList.contains('open')"
        )
        mark_count = browser_page.evaluate("document.querySelectorAll('mark.global-search-hit').length")
        assert mark_count == 0, f"Expected highlights to clear on Escape, got {mark_count}"

    def test_collapsed_section_auto_expands_on_match(self, browser_page, trace_entries):
        _, entries, _, message_term_info = trace_entries
        _, target_entry_idx = message_term_info
        message_term, _ = message_term_info

        # Select the entry that yielded message search term.
        browser_page.locator(".sidebar-item").nth(min(target_entry_idx, len(entries) - 1)).click()
        browser_page.wait_for_timeout(120)

        # Collapse the messages section (identified by message blocks).
        msg_section = browser_page.locator(".section", has=browser_page.locator(".msg")).first
        msg_body = msg_section.locator(".section-body")
        msg_section.locator(".section-header").click()
        browser_page.wait_for_timeout(120)
        assert "open" not in msg_body.get_attribute("class")

        _dispatch_find_shortcut(browser_page, meta=False, ctrl=True)
        browser_page.fill("#global-search-input", message_term)

        browser_page.wait_for_function(
            """() => {
                const section = [...document.querySelectorAll('.section')].find(s => s.querySelector('.msg'));
                if (!section) return false;
                const body = section.querySelector('.section-body');
                return body && body.classList.contains('open');
            }"""
        )
