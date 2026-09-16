#!/usr/bin/env python3
"""Render a recorded trace and capture the tool-output-size evidence screenshots.

Usage:
    uv run python .agents/evidence/pr/tool-bloat-detector/capture.py \
        .traces/tool-bloat-evidence/trace_tool_bloat.jsonl

The JSONL must come from a real `claude-tap` run (see README.md for the
recording command).  The script generates the viewer HTML next to the JSONL,
opens it, clicks the sidebar entry carrying the size badge, and writes both
screenshots into this directory.
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

from claude_tap.viewer import _generate_html_viewer  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2

    trace_path = Path(argv[1]).resolve()
    if not trace_path.exists():
        print(f"trace not found: {trace_path}")
        return 1

    html_path = trace_path.with_suffix(".html")
    _generate_html_viewer(trace_path, html_path)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(html_path.as_uri(), wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(2500)

        badged = None
        for item in page.query_selector_all(".sidebar-item"):
            if item.query_selector(".si-bloat-badge"):
                badged = item
                break
        if badged is None:
            print("no sidebar entry carries a size badge; is this the right trace?")
            browser.close()
            return 1

        page.screenshot(path=str(OUT_DIR / "trace-viewer-tool-bloat-sidebar-badge.png"))
        badged.click()
        page.wait_for_timeout(1500)

        alerts = page.query_selector_all("#detail .tool-bloat-alert")
        if not alerts:
            print("detail view shows no size banner")
            browser.close()
            return 1
        alerts[0].scroll_into_view_if_needed()
        page.wait_for_timeout(600)
        page.screenshot(path=str(OUT_DIR / "trace-viewer-tool-bloat-detail-banner.png"))

        print(f"sidebar badge: {badged.query_selector('.si-bloat-badge').inner_text()}")
        print(f"detail banners: {[a.inner_text() for a in alerts]}")
        print(f"page errors: {errors}")
        browser.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
