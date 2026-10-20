#!/usr/bin/env python3
"""Regenerate the ttft_ms trace evidence from an actual proxy run.

Usage (from repo root):

    uv run python .agents/evidence/pr/ttft/run_proxy_and_capture.py

This writes:

- `.traces/ttft/traces.sqlite3` (gitignored local store)
- `.agents/evidence/pr/ttft/dashboard-ttft-session.png`
- `.agents/evidence/pr/ttft/dashboard-ttft-record-json.png`

Unlike a hand-seeded store, the records are produced by the real reverse
proxy code path under review: a local SSE upstream is served over real TCP,
the actual `proxy_handler` aiohttp application forwards two streaming
`/v1/messages` requests to it, and the resulting records flow through
`_build_record` -> `TraceWriter` -> `TraceStore`. The script asserts every
streaming record carries `ttft_ms` within `[0, duration_ms]` before the
dashboard screenshots are captured from that same store.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
EVIDENCE_DIR = Path(__file__).resolve().parent
DB_PATH = REPO_ROOT / ".traces" / "ttft" / "traces.sqlite3"
SCREENSHOT_PATH = EVIDENCE_DIR / "dashboard-ttft-session.png"
JSON_SCREENSHOT_PATH = EVIDENCE_DIR / "dashboard-ttft-record-json.png"


def _sse_frames(turn: int) -> bytes:
    return b"".join(
        [
            b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_ttft_%d","type":"message","role":"assistant","model":"claude-sonnet-4-6","content":[],"usage":{"input_tokens":12,"output_tokens":1}}}\n\n'
            % turn,
            b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Streaming turn %d captured by the real proxy with ttft_ms."}}\n\n'
            % turn,
            b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ]
    )


async def _run_proxy() -> str:
    """Serve a local SSE upstream, forward two streaming turns through the
    real reverse proxy, and return the resulting trace session id."""
    import aiohttp
    from aiohttp import web

    from claude_tap.proxy import proxy_handler
    from claude_tap.trace import TraceWriter
    from claude_tap.trace_store import get_trace_store

    async def upstream_handler(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        turn = 1 if "first" in body["messages"][0]["content"] else 2
        frames = _sse_frames(turn)
        response = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        # Emulate real LLM timing: a first-token latency before the first
        # byte, then drip the rest so ttft_ms and duration_ms diverge.
        await asyncio.sleep(0.08)
        third = len(frames) // 3
        await response.write(frames[:third])
        await asyncio.sleep(0.05)
        await response.write(frames[third : 2 * third])
        await asyncio.sleep(0.05)
        await response.write(frames[2 * third :])
        await response.write_eof()
        return response

    upstream_app = web.Application()
    upstream_app.router.add_post("/{path_info:.*}", upstream_handler)
    upstream_runner = web.AppRunner(upstream_app)
    await upstream_runner.setup()
    upstream_site = web.TCPSite(upstream_runner, "127.0.0.1", 0)
    await upstream_site.start()
    upstream_port = upstream_site._server.sockets[0].getsockname()[1]

    store = get_trace_store()
    session_id = store.create_session(client="claude", proxy_mode="reverse")
    writer = TraceWriter(session_id, store=store)

    proxy_session = aiohttp.ClientSession(auto_decompress=False)
    app = web.Application(client_max_size=0)
    app["trace_ctx"] = {
        "target_url": f"http://127.0.0.1:{upstream_port}",
        "writer": writer,
        "session": proxy_session,
        "turn_counter": 0,
        "store_stream_events": True,
        "capture_only": False,
    }
    app.router.add_route("*", "/{path_info:.*}", proxy_handler)
    proxy_runner = web.AppRunner(app)
    await proxy_runner.setup()
    proxy_site = web.TCPSite(proxy_runner, "127.0.0.1", 0)
    await proxy_site.start()
    proxy_port = proxy_site._server.sockets[0].getsockname()[1]

    try:
        async with aiohttp.ClientSession(auto_decompress=False) as client:
            for prompt in ("first streaming turn", "second streaming turn"):
                async with client.post(
                    f"http://127.0.0.1:{proxy_port}/v1/messages",
                    json={
                        "model": "claude-sonnet-4-6",
                        "stream": True,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                ) as response:
                    assert response.status == 200
                    await response.read()
    finally:
        await proxy_session.close()
        await proxy_runner.cleanup()
        await upstream_runner.cleanup()

    records = store.load_records(session_id)
    assert len(records) == 2, f"expected 2 recorded turns, got {len(records)}"
    for record in records:
        assert "ttft_ms" in record, f"streaming record missing ttft_ms: {record.keys()}"
        assert 0 <= record["ttft_ms"] <= record["duration_ms"]
    store.finalize_session(session_id, {"api_calls": len(records)})
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
    os.environ["CLOUDTAP_DB"] = str(DB_PATH)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    lock_path = Path(str(DB_PATH) + ".write.lock")
    if lock_path.exists():
        lock_path.unlink()

    from claude_tap.trace_store import reset_trace_store

    reset_trace_store()

    async def run() -> str:
        session_id = await _run_proxy()
        return session_id

    session_id = asyncio.run(run())
    asyncio.run(_capture(session_id))
    print(f"db={DB_PATH}")
    print(f"screenshot={SCREENSHOT_PATH}")
    print(f"json_screenshot={JSON_SCREENSHOT_PATH}")
    print(f"session_id={session_id}")
    print(
        f"records={json.dumps([{'ttft_ms': r['ttft_ms'], 'duration_ms': r['duration_ms']} for r in _load_records(DB_PATH)])}"
    )


def _load_records(db_path: Path) -> list[dict]:
    os.environ["CLOUDTAP_DB"] = str(db_path)
    from claude_tap.trace_store import get_trace_store

    store = get_trace_store()
    session_id = store.list_session_rows()[-1]["id"]
    return store.load_records(session_id)


if __name__ == "__main__":
    main()
