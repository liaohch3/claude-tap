#!/usr/bin/env python3
"""Capture dashboard evidence for Cursor transcript-only mode.

Usage (from repo root):

    uv run python .agents/evidence/pr/418-cursor-transcript-only/seed_and_capture.py

Writes:

- `.traces/418-cursor-transcript-only/traces.sqlite3`
- `.agents/evidence/pr/418-cursor-transcript-only/dashboard-cursor-sessions.png`
- `.agents/evidence/pr/418-cursor-transcript-only/dashboard-cursor-session-detail.png`
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
EVIDENCE_DIR = Path(__file__).resolve().parent
DB_PATH = REPO_ROOT / ".traces" / "418-cursor-transcript-only" / "traces.sqlite3"
SESSIONS_SHOT = EVIDENCE_DIR / "dashboard-cursor-sessions.png"
DETAIL_SHOT = EVIDENCE_DIR / "dashboard-cursor-session-detail.png"
CURSOR_TRANSCRIPT_ID = "0b95c4b6-03e2-4780-8c3a-124f43625297"


def _cursor_record(turn: int, step: int, *, user: str, tools: list[str], model: str = "grok-4.5") -> dict:
    content: list[dict] = [{"type": "text", "text": f"assistant step {step}"}]
    for index, name in enumerate(tools, start=1):
        content.append(
            {
                "type": "tool_use",
                "id": f"cursor_tool_{turn}_{index}",
                "name": name,
                "input": {"path": "README.md"} if name == "Read" else {"command": "git status"},
            }
        )
    return {
        "timestamp": f"2026-08-02T04:00:{turn:02d}+00:00",
        "turn": turn,
        "duration_ms": 0,
        "transport": "cursor-transcript",
        "request": {
            "method": "CURSOR_TRANSCRIPT",
            "path": f"/cursor/transcript/{CURSOR_TRANSCRIPT_ID}/turn/1/step/{step}",
            "headers": {},
            "body": {
                "cursor_turn": 1,
                "cursor_step": step,
                "messages": [{"role": "user", "content": user}],
                "model": model,
                "cursor_meta_source": "chat-store",
            },
        },
        "response": {
            "status": 200,
            "headers": {},
            "body": {
                "id": CURSOR_TRANSCRIPT_ID,
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": content,
            },
        },
        "capture": {
            "client": "cursor",
            "proxy_mode": "transcript",
            "cursor_transcript_id": CURSOR_TRANSCRIPT_ID,
            "cursor_project": "Users-youngcan-claude-tap",
        },
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
    session_id = store.create_session(client="cursor", proxy_mode="transcript")
    user = "这是一个什么项目 当前的改动你觉得可不可以"
    records = [
        _cursor_record(1, 1, user=user, tools=["Read", "Shell", "Read"]),
        _cursor_record(2, 2, user=user, tools=["Shell"]),
        _cursor_record(3, 3, user=user, tools=["Read"]),
    ]
    for record in records:
        store.append_record(session_id, record)
    store.finalize_session(session_id, {"api_calls": len(records), "models_used": {"grok-4.5": 3}})
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
            await page.wait_for_selector("text=这是一个什么项目", timeout=10000)
            await page.wait_for_selector("text=grok-4.5", timeout=10000)
            await page.wait_for_timeout(300)
            await page.screenshot(path=str(SESSIONS_SHOT), full_page=False)

            # Dashboard session detail is a compact timeline (not the full viewer sidebar).
            await page.goto(
                f"http://127.0.0.1:{port}/dashboard/session/{session_id}",
                wait_until="domcontentloaded",
                timeout=15000,
            )
            await page.wait_for_selector("text=/cursor/transcript/", timeout=15000)
            # first-message nodes can stay attached-but-hidden until a turn is expanded.
            await page.locator(".first-message").first.wait_for(state="attached", timeout=15000)
            await page.get_by_text("Turn 1", exact=False).first.wait_for(state="attached", timeout=15000)
            await page.wait_for_timeout(400)
            await page.screenshot(path=str(DETAIL_SHOT), full_page=False)
            await browser.close()
    finally:
        await server.stop()


def main() -> None:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    session_id = _seed_store()
    asyncio.run(_capture(session_id))
    print(f"db={DB_PATH}")
    print(f"session_id={session_id}")
    print(f"sessions_shot={SESSIONS_SHOT}")
    print(f"detail_shot={DETAIL_SHOT}")


if __name__ == "__main__":
    main()
