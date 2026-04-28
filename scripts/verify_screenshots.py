#!/usr/bin/env python3
"""Verify viewer HTML renders correctly — catches broken/empty/raw-JSON pages.

Usage: uv run python scripts/verify_screenshots.py .traces/trace_*.html

Exit 0 = all OK, exit 1 = problems found.
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

# Force UTF-8 stdout/stderr so emoji output works on Windows GBK consoles.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")


def verify_viewer_html(html_path: str) -> list[str]:
    issues: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(Path(html_path).absolute().as_uri(), wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2000)
        if errors:
            issues.append(f"JS errors: {errors}")
        body_text = page.inner_text("body")[:500]
        if '"type":"tool_use"' in body_text or "JSONDecodeError" in body_text:
            issues.append("Page shows raw JSON dump or Python errors")
        if not page.query_selector(".sidebar"):
            issues.append("No sidebar — viewer not rendered")
        if len(page.query_selector_all(".sidebar-item")) == 0:
            issues.append("No sidebar entries — viewer empty")
        if not page.query_selector("#detail"):
            issues.append("No detail panel")
        browser.close()
    return issues


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: verify_screenshots.py <file.html> ...")
        sys.exit(2)
    all_ok = True
    for f in sys.argv[1:]:
        problems = verify_viewer_html(f)
        if problems:
            print(f"❌ {f}:")
            for p in problems:
                print(f"   - {p}")
            all_ok = False
        else:
            print(f"✅ {f}: OK")
    sys.exit(0 if all_ok else 1)
