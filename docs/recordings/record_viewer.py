"""Record a walkthrough of the claude-tap trace viewer using Playwright.

Produces:
  - Video at 1440x900 in docs/recordings/video/
  - 10+ high-quality screenshots in docs/recordings/
"""

from __future__ import annotations

import pathlib
import time

from playwright.sync_api import sync_playwright

TRACE_HTML = "/tmp/codex-tap-demo/.traces/trace_20260228_004827.html"
SCREENSHOT_DIR = pathlib.Path(__file__).parent
VIDEO_DIR = SCREENSHOT_DIR / "video"
WIDTH, HEIGHT = 1440, 900


def _wait(ms: int = 600) -> None:
    time.sleep(ms / 1000)


def _screenshot(page, name: str) -> None:
    path = SCREENSHOT_DIR / f"{name}.png"
    page.screenshot(path=str(path), full_page=False)
    print(f"  screenshot: {path.name}")


def main() -> None:
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": WIDTH, "height": HEIGHT},
            record_video_dir=str(VIDEO_DIR),
            record_video_size={"width": WIDTH, "height": HEIGHT},
        )
        page = context.new_page()
        page.goto(f"file://{TRACE_HTML}")
        page.wait_for_load_state("networkidle")
        _wait(800)

        # --- Step 1: Click Turn 1 -> expand Tools & SSE Events ---
        print("Step 1: Turn 1 + expand Tools & SSE Events")
        page.mouse.click(150, 125)
        _wait(500)
        _screenshot(page, "viewer-01-turn1-overview")

        # Expand "Tools" section
        page.evaluate("""() => {
            const headers = document.querySelectorAll('.section-header');
            for (const h of headers) {
                if (h.textContent.includes('Tools')) { h.click(); break; }
            }
        }""")
        _wait(400)

        # Expand "SSE Events" section
        page.evaluate("""() => {
            const headers = document.querySelectorAll('.section-header');
            for (const h of headers) {
                if (h.textContent.includes('SSE Events')) { h.click(); break; }
            }
        }""")
        _wait(400)
        _screenshot(page, "viewer-02-tools-sse-expanded")

        # --- Step 2: Click "Request JSON" -> expand "Full JSON" -> scroll ---
        print("Step 2: Request JSON + Full JSON + scroll")
        page.evaluate("""() => {
            const btns = document.querySelectorAll('.act-btn');
            for (const b of btns) {
                if (b.textContent.includes('Request')) { b.click(); break; }
            }
        }""")
        _wait(400)

        # Expand "Full JSON"
        page.evaluate("""() => {
            const headers = document.querySelectorAll('.section-header');
            for (const h of headers) {
                if (h.textContent.includes('Full JSON')) { h.click(); break; }
            }
        }""")
        _wait(400)

        # Scroll detail panel down
        page.evaluate("document.getElementById('detail').scrollTop = 500")
        _wait(400)
        _screenshot(page, "viewer-03-request-json-scrolled")

        # --- Step 3: Click Turn 5 ---
        print("Step 3: Turn 5")
        page.mouse.click(150, 401)
        _wait(500)
        _screenshot(page, "viewer-04-turn5")

        # --- Step 4: Click "Diff with Prev" ---
        print("Step 4: Diff with Prev")
        page.evaluate("""() => {
            const btns = document.querySelectorAll('.act-btn');
            for (const b of btns) {
                if (b.textContent.includes('Diff')) { b.click(); break; }
            }
        }""")
        _wait(600)
        _screenshot(page, "viewer-05-diff")

        # Close diff overlay
        page.evaluate("document.querySelector('.diff-close')?.click()")
        _wait(300)

        # --- Step 5: Click "cURL" ---
        print("Step 5: cURL")
        page.evaluate("""() => {
            const btns = document.querySelectorAll('.act-btn');
            for (const b of btns) {
                if (b.textContent.includes('cURL')) { b.click(); break; }
            }
        }""")
        _wait(400)
        _screenshot(page, "viewer-06-curl")

        # --- Step 6: Click Turn 10 ---
        print("Step 6: Turn 10")
        page.mouse.click(150, 746)
        _wait(500)
        _screenshot(page, "viewer-07-turn10")

        # --- Step 7: Toggle dark mode ---
        print("Step 7: Dark mode toggle")
        # Dismiss any overlay that might be blocking
        page.evaluate("document.querySelector('.diff-overlay')?.remove()")
        _wait(200)
        page.click("#theme-toggle")
        _wait(500)
        _screenshot(page, "viewer-08-dark-mode")

        # --- Step 8: Scroll sidebar down ---
        print("Step 8: Scroll sidebar to show turns 11-18")
        page.evaluate("document.getElementById('sidebar').scrollTop = 700")
        _wait(400)
        _screenshot(page, "viewer-09-sidebar-scrolled")

        # --- Step 9: Click last visible sidebar item ---
        print("Step 9: Click last visible turn")
        page.evaluate("""() => {
            const items = document.querySelectorAll('.sidebar-item');
            if (items.length > 0) items[items.length - 1].click();
        }""")
        _wait(500)
        _screenshot(page, "viewer-10-last-turn")

        # --- Step 10: Final wide screenshot ---
        print("Step 10: Final wide screenshot")
        _screenshot(page, "viewer-11-final-wide")

        # Close to finalize video
        context.close()
        browser.close()

    print(f"\nDone. Video saved to {VIDEO_DIR}/")
    print(f"Screenshots saved to {SCREENSHOT_DIR}/")


if __name__ == "__main__":
    main()
