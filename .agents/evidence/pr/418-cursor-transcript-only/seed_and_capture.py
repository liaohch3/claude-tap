#!/usr/bin/env python3
"""Capture dashboard evidence from a real Cursor agent transcript.

Usage (from repo root):

    uv run python .agents/evidence/pr/418-cursor-transcript-only/seed_and_capture.py

This imports a real local Cursor JSONL (nested layout under
``~/.cursor/projects/.../agent-transcripts/<id>/<id>.jsonl``) into
``.traces/418-cursor-transcript-only/traces.sqlite3`` and screenshots the
live dashboard. Screenshots are therefore backed by real transcript data,
not fabricated request/response rows.

Writes:

- `.traces/418-cursor-transcript-only/traces.sqlite3`
- `.agents/evidence/pr/418-cursor-transcript-only/dashboard-cursor-sessions.png`
- `.agents/evidence/pr/418-cursor-transcript-only/dashboard-cursor-session-detail.png`
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
EVIDENCE_DIR = Path(__file__).resolve().parent
TRACE_DIR = REPO_ROOT / ".traces" / "418-cursor-transcript-only"
DB_PATH = TRACE_DIR / "traces.sqlite3"
CURSOR_HOME = TRACE_DIR / "cursor-home"
SESSIONS_SHOT = EVIDENCE_DIR / "dashboard-cursor-sessions.png"
DETAIL_SHOT = EVIDENCE_DIR / "dashboard-cursor-session-detail.png"

# Real nested Cursor transcript captured during Cursor IDE use on this machine.
REAL_TRANSCRIPT = (
    Path.home()
    / ".cursor"
    / "projects"
    / "Users-youngcan-claude-tap"
    / "agent-transcripts"
    / "0b95c4b6-03e2-4780-8c3a-124f43625297"
    / "0b95c4b6-03e2-4780-8c3a-124f43625297.jsonl"
)
PROJECT_SLUG = "Users-youngcan-claude-tap"
CURSOR_SESSION_ID = "0b95c4b6-03e2-4780-8c3a-124f43625297"


def _stage_real_transcript() -> Path:
    if not REAL_TRANSCRIPT.is_file():
        raise SystemExit(
            f"Real Cursor transcript not found: {REAL_TRANSCRIPT}\n"
            "Open a Cursor Agent chat in this repo first, then re-run."
        )
    if CURSOR_HOME.exists():
        shutil.rmtree(CURSOR_HOME)
    dest = (
        CURSOR_HOME
        / ".cursor"
        / "projects"
        / PROJECT_SLUG
        / "agent-transcripts"
        / CURSOR_SESSION_ID
        / f"{CURSOR_SESSION_ID}.jsonl"
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REAL_TRANSCRIPT, dest)
    return dest


async def _import_real_transcript() -> str:
    os.environ["CLOUDTAP_DB"] = str(DB_PATH)
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    lock_path = Path(str(DB_PATH) + ".write.lock")
    if lock_path.exists():
        lock_path.unlink()

    from claude_tap.cursor_transcript import import_cursor_transcripts
    from claude_tap.trace_store import get_trace_store, reset_trace_store

    reset_trace_store()
    store = get_trace_store()
    watcher = await import_cursor_transcripts(since=0, home=CURSOR_HOME, store=store)
    try:
        if not watcher.session_ids:
            raise SystemExit("Import produced no Cursor sessions from the real transcript")
        # Prefer the nested-layout session for the detail screenshot.
        for session_id in watcher.session_ids:
            records = store.load_records(session_id)
            if not records:
                continue
            if records[0].get("capture", {}).get("cursor_project") == PROJECT_SLUG:
                model = str((records[0].get("request") or {}).get("body", {}).get("model") or "")
                summary = {"api_calls": len(records)}
                if model:
                    summary["models_used"] = {model: len(records)}
                store.finalize_session(session_id, summary)
                return session_id
        session_id = watcher.session_ids[0]
        records = store.load_records(session_id)
        store.finalize_session(session_id, {"api_calls": len(records)})
        return session_id
    finally:
        watcher.close()


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
            await page.wait_for_selector("text=Cursor", timeout=10000)
            await page.wait_for_timeout(400)
            await page.screenshot(path=str(SESSIONS_SHOT), full_page=False)

            await page.goto(
                f"http://127.0.0.1:{port}/dashboard/session/{session_id}",
                wait_until="domcontentloaded",
                timeout=15000,
            )
            await page.wait_for_selector("text=/cursor/transcript/", timeout=15000)
            await page.locator(".first-message").first.wait_for(state="attached", timeout=15000)
            await page.get_by_text("Turn 1", exact=False).first.wait_for(state="attached", timeout=15000)
            await page.wait_for_timeout(400)
            await page.screenshot(path=str(DETAIL_SHOT), full_page=False)
            await browser.close()
    finally:
        await server.stop()


def main() -> None:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    staged = _stage_real_transcript()
    session_id = asyncio.run(_import_real_transcript())
    asyncio.run(_capture(session_id))
    print(f"source={REAL_TRANSCRIPT}")
    print(f"staged={staged}")
    print(f"db={DB_PATH}")
    print(f"session_id={session_id}")
    print(f"sessions_shot={SESSIONS_SHOT}")
    print(f"detail_shot={DETAIL_SHOT}")


if __name__ == "__main__":
    main()
