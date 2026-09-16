#!/usr/bin/env python3
"""Render a recorded trace and capture the session-briefing banner.

Usage:
    uv run python .agents/evidence/pr/session-briefing/capture.py \
        .traces/cache-invalidation-diagnostics/trace_cache_diagnostics.jsonl \
        trace-viewer-session-briefing-cache.png

Pass --dashboard to render the online viewer the dashboard session page serves,
which reaches the briefing through metadata stubs plus the full records.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

from claude_tap.viewer import _generate_html_viewer  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent


def _render_dashboard_page(trace_path: Path, html_path: Path) -> None:
    """Render the online viewer the way the dashboard session page does."""
    import json

    from claude_tap.viewer import _extract_metadata_from_record, _generate_html_viewer_from_metadata

    records = []
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    metadata = [item for record in records if (item := _extract_metadata_from_record(record)) is not None]
    _generate_html_viewer_from_metadata(
        metadata,
        html_path,
        display_trace_path="/api/sessions/demo/export/compact",
        display_html_path="/dashboard/session/demo",
        records_api_path="/api/sessions/demo/records",
        briefing_records=records,
    )


def main(argv: list[str]) -> int:
    args = [item for item in argv[1:] if item != "--dashboard"]
    dashboard = "--dashboard" in argv
    if len(args) != 2:
        print(__doc__)
        return 2

    trace_path = Path(args[0]).resolve()
    out_name = args[1]
    if not trace_path.exists():
        print(f"trace not found: {trace_path}")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        html_path = Path(tmp) / "briefing.html"
        if dashboard:
            _render_dashboard_page(trace_path, html_path)
        else:
            _generate_html_viewer(trace_path, html_path)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
            errors: list[str] = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.goto(html_path.as_uri(), wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector("#session-briefing.is-visible", timeout=10000)
            page.wait_for_timeout(800)
            page.screenshot(path=str(OUT_DIR / out_name))
            print(page.locator("#session-briefing").inner_text())
            print(f"page errors: {errors}")
            browser.close()

    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
