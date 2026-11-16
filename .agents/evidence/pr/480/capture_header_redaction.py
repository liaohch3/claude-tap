from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from pathlib import Path

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
from playwright.async_api import async_playwright, expect

from claude_tap.compact_trace import build_compact_trace_bundle
from claude_tap.proxy import proxy_handler
from claude_tap.trace import TraceWriter
from claude_tap.trace_log_handler import SQLiteLogHandler
from claude_tap.trace_store import TraceStore
from claude_tap.viewer import _generate_html_viewer_from_compact_bundle

REPO_ROOT = Path(__file__).resolve().parents[4]
EVIDENCE_DIR = Path(__file__).resolve().parent
AUTH_HEADERS = {
    "Authorization": "Bearer AUTH_EVIDENCE_SENTINEL_NO_CREDENTIAL",
    "x-api-key": "API_EVIDENCE_SENTINEL_NO_CREDENTIAL",
}


async def capture_requests(trace_dir: Path) -> list[dict]:
    received = []

    async def upstream_handler(request: web.Request) -> web.StreamResponse:
        received.append({name: request.headers[name] for name in AUTH_HEADERS})
        body = await request.json()
        text = f"Acknowledged turn {len(received)}."
        payload = {
            "id": f"msg_evidence_{len(received)}",
            "type": "message",
            "role": "assistant",
            "model": body["model"],
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 24, "output_tokens": 8},
        }
        if not body.get("stream"):
            return web.json_response(payload, headers=AUTH_HEADERS)
        response = web.StreamResponse(headers={**AUTH_HEADERS, "Content-Type": "text/event-stream"})
        await response.prepare(request)
        for event, data in (
            ("message_start", {"type": "message_start", "message": {**payload, "content": []}}),
            (
                "content_block_start",
                {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            ),
            (
                "content_block_delta",
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
            ),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            (
                "message_delta",
                {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 8}},
            ),
            ("message_stop", {"type": "message_stop"}),
        ):
            await response.write(f"event: {event}\ndata: {json.dumps(data)}\n\n".encode())
        await response.write_eof()
        return response

    store = TraceStore(trace_dir / "traces.sqlite3")
    session_id = store.create_session(client="claude", proxy_mode="reverse")
    writer = TraceWriter(session_id, store=store)
    logger = logging.getLogger("claude-tap")
    handler = SQLiteLogHandler(session_id, store=store)
    original_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    upstream_app = web.Application()
    upstream_app.router.add_post("/v1/messages", upstream_handler)
    try:
        async with TestServer(upstream_app) as upstream, aiohttp.ClientSession(trust_env=False) as client:
            proxy_app = web.Application()
            proxy_app["trace_ctx"] = {
                "target_url": str(upstream.make_url("/")).rstrip("/"),
                "writer": writer,
                "session": client,
                "store_stream_events": True,
            }
            proxy_app.router.add_post("/v1/messages", proxy_handler)
            messages = [{"role": "user", "content": "Acknowledge this first turn."}]
            async with TestServer(proxy_app) as proxy:
                for streaming in (False, True):
                    async with client.post(
                        proxy.make_url("/v1/messages"),
                        headers=AUTH_HEADERS,
                        json={"model": "claude-evidence", "max_tokens": 32, "messages": messages, "stream": streaming},
                    ) as response:
                        assert response.status == 200
                        await response.read()
                    messages.extend(
                        [
                            {"role": "assistant", "content": "Acknowledged turn 1."},
                            {"role": "user", "content": "Acknowledge this second turn using a stream."},
                        ]
                    )
        assert received == [AUTH_HEADERS, AUTH_HEADERS]
        writer.close()
        records = store.load_records(session_id)
        assert len(records) == 2
        for record in records:
            for direction in ("request", "response"):
                headers = {name.lower(): value for name, value in record[direction]["headers"].items()}
                assert headers["authorization"] == headers["x-api-key"] == "***"
        (trace_dir / "trace.jsonl").write_text(store.export_jsonl(session_id), encoding="utf-8")
        (trace_dir / "trace.ctap.json").write_text(store.export_compact(session_id), encoding="utf-8")
        (trace_dir / "proxy.log").write_text(store.export_log(session_id), encoding="utf-8")
        _generate_html_viewer_from_compact_bundle(
            build_compact_trace_bundle(records),
            trace_dir / "trace.html",
            display_trace_path="trace.ctap.json",
            display_html_path="trace.html",
        )
        for artifact in trace_dir.iterdir():
            if artifact.is_file():
                contents = artifact.read_bytes()
                for value in AUTH_HEADERS.values():
                    assert value.encode() not in contents
                    assert value[:12].encode() not in contents
        return records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(original_level)
        handler.close()
        store.close()


async def capture_viewer(trace_dir: Path) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 1000}, locale="en-US")
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.route("https://**", lambda route: route.abort())
        try:
            await page.goto((trace_dir / "trace.html").as_uri(), wait_until="networkidle")
            await expect(page.locator(".sidebar-item")).to_have_count(2)
            await page.locator(".sidebar-item").last.click()
            await expect(page.locator("#detail")).to_contain_text("Acknowledged turn 2.")
            await page.screenshot(path=EVIDENCE_DIR / "trace-viewer-multiturn.png")
            await page.locator(".sidebar-item").first.click()
            for header in await page.locator("#detail .section-header").all():
                if await header.locator("+ .section-body").evaluate("element => element.classList.contains('open')"):
                    await header.click()
            section = page.locator("#detail .section").filter(has=page.locator(".title", has_text="Full JSON"))
            await section.locator(".section-header").click()
            await expect(section).to_contain_text('"Authorization"')
            await expect(section).to_contain_text('"x-api-key"')
            await expect(section).to_contain_text('"***"')
            assert await page.evaluate("entries[0].request.headers.Authorization") == "***"
            assert await page.evaluate("entries[0].request.headers['x-api-key']") == "***"
            await section.scroll_into_view_if_needed()
            await page.screenshot(path=EVIDENCE_DIR / "trace-viewer-masked-headers.png")
            assert not errors, errors
        finally:
            await browser.close()


async def main() -> None:
    trace_root = REPO_ROOT / ".traces"
    trace_root.mkdir(exist_ok=True)
    trace_dir = Path(tempfile.mkdtemp(prefix="pr480-header-redaction-", dir=trace_root))
    await capture_requests(trace_dir)
    await capture_viewer(trace_dir)
    print(f"Captured two proxy requests and viewer screenshots from {trace_dir.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    asyncio.run(main())
