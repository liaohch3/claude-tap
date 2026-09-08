#!/usr/bin/env python3
"""Deterministically recreate the ttft_ms trace evidence.

Usage (from repo root):

    uv run python .agents/evidence/pr/ttft/seed_and_capture.py

This writes:

- `.traces/ttft/traces.sqlite3` (gitignored local store)
- `.agents/evidence/pr/ttft/dashboard-ttft-session.png`
- `.agents/evidence/pr/ttft/dashboard-ttft-record-json.png`

The seeded records use the exact top-level shape `_build_record` produces for
streaming responses, including the `ttft_ms` field: the wall-clock delta
between sending the upstream request and receiving the first upstream byte.
The script also asserts the field survives the TraceStore round-trip, so the
committed screenshot always corresponds to records that carry `ttft_ms`.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
EVIDENCE_DIR = Path(__file__).resolve().parent
DB_PATH = REPO_ROOT / ".traces" / "ttft" / "traces.sqlite3"
SCREENSHOT_PATH = EVIDENCE_DIR / "dashboard-ttft-session.png"
JSON_SCREENSHOT_PATH = EVIDENCE_DIR / "dashboard-ttft-record-json.png"


def _streaming_record(turn: int, ttft_ms: int, duration_ms: int, text: str) -> dict:
    return {
        "timestamp": f"2026-09-09T08:0{turn}:00+00:00",
        "request_id": f"req_ttft_{turn}",
        "turn": turn,
        "duration_ms": duration_ms,
        "ttft_ms": ttft_ms,
        "request": {
            "method": "POST",
            "path": "/v1/messages",
            "headers": {"x-api-key": "sk-ant-...redacted", "anthropic-version": "2023-06-01"},
            "body": {
                "model": "claude-sonnet-4-6",
                "stream": True,
                "messages": [{"role": "user", "content": "ttft evidence seed"}],
            },
        },
        "response": {
            "status": 200,
            "headers": {"Content-Type": "text/event-stream"},
            "body": {
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
                "usage": {"input_tokens": 12, "output_tokens": 8},
            },
            "sse_events": [
                {"event": "message_start", "data": {"type": "message_start"}},
                {"event": "content_block_delta", "data": {"type": "content_block_delta", "delta": {"text": text}}},
                {"event": "message_stop", "data": {"type": "message_stop"}},
            ],
        },
        "upstream_base_url": "https://api.anthropic.com",
    }


def _seed_store() -> str:
    os.environ["CLOUDTAP_DB"] = str(DB_PATH)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    lock_path = Path(str(DB_PATH) + ".write.lock")
    if lock_path.exists():
        lock_path.unlink()

    from claude_tap.trace_store import get_trace_store, reset_trace_store

    reset_trace_store()
    store = get_trace_store()
    session_id = store.create_session(client="claude", proxy_mode="reverse")
    for turn, (ttft_ms, duration_ms, text) in enumerate(
        [
            (412, 1873, "First streaming turn captured with ttft_ms."),
            (297, 1210, "Second streaming turn captured with ttft_ms."),
        ],
        start=1,
    ):
        store.append_record(session_id, _streaming_record(turn, ttft_ms, duration_ms, text))

    records = store.load_records(session_id)
    assert [r.get("ttft_ms") for r in records] == [412, 297], "ttft_ms must survive the store round-trip"
    assert all(r["ttft_ms"] < r["duration_ms"] for r in records)

    store.finalize_session(session_id, {"api_calls": 2})
    return session_id


async def _capture(session_id: str) -> None:
    from playwright.async_api import async_playwright

    from claude_tap.live import LiveViewerServer

    server = LiveViewerServer(port=0, dashboard_mode=True)
    port = await server.start()
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1440, "height": 900})
            await page.goto(f"http://127.0.0.1:{port}/dashboard", wait_until="domcontentloaded", timeout=15000)
            row = page.locator(f'[data-session="{session_id}"]')
            await row.wait_for(state="visible", timeout=10000)
            await row.click()
            await page.wait_for_timeout(300)
            await page.screenshot(path=str(SCREENSHOT_PATH), full_page=False)

            # Expand Turn 1's raw JSON panel so the captured `ttft_ms` field is visible.
            await page.locator(".json-btn").first.click()
            pre = page.locator("[data-json-index='0']")
            await page.wait_for_function(
                "() => (document.querySelector(\"[data-json-index='0']\")?.textContent || '').includes('ttft_ms')",
                timeout=5000,
            )
            await page.wait_for_timeout(200)
            await page.screenshot(path=str(JSON_SCREENSHOT_PATH), full_page=False)
            assert "ttft_ms" in (await pre.text_content() or "")
            await browser.close()
    finally:
        await server.stop()


def main() -> None:
    session_id = _seed_store()
    asyncio.run(_capture(session_id))
    print(f"db={DB_PATH}")
    print(f"screenshot={SCREENSHOT_PATH}")
    print(f"session_id={session_id}")


if __name__ == "__main__":
    main()
