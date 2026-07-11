"""Continuation-chain detection across stored trace sessions."""

from datetime import datetime, timezone

import aiohttp
import pytest

from claude_tap.continuation import (
    COMPACT_RESUME_MARKER,
    find_continuation_chains,
    reset_fingerprint_cache,
)
from claude_tap.live import MAX_COMPACTION_SESSIONS, LiveViewerServer
from claude_tap.trace_store import get_trace_store

_SYSTEM_A = (
    "You are Claude Code.\n"
    "Here is useful information about the environment you are running in:\n"
    " - Primary working directory: /tmp/project-alpha\n"
    " - Platform: darwin\n"
)
_SYSTEM_B = _SYSTEM_A.replace("/tmp/project-alpha", "/tmp/project-beta")

_SUMMARY_TEXT = (
    "1. Primary Request and Intent: The user asked for a full read-through of the dashboard "
    "and viewer sources, then a summary of each file's responsibilities and three small "
    "improvement ideas. 2. Key Technical Concepts: SSE streaming, SQLite trace storage, "
    "lab experiment cards. 3. Files Read: dashboard.html, viewer.html, live.py. "
    "4. Pending Tasks: summarize renderers.js and propose improvements."
)


@pytest.fixture(autouse=True)
def _fresh_fingerprints():
    reset_fingerprint_cache()
    yield
    reset_fingerprint_cache()


def _main_record(turn, messages, *, system=_SYSTEM_A, response_text="Understood.", usage=None):
    return {
        "timestamp": "2026-07-10T22:00:00+00:00",
        "request_id": f"req_chain_{turn}",
        "turn": turn,
        "duration_ms": 900,
        "capture": {"client": "claude", "proxy_mode": "reverse"},
        "request": {
            "method": "POST",
            "path": "/v1/messages?beta=true",
            "headers": {"Host": "api.anthropic.com"},
            "body": {
                "model": "claude-opus-4-8",
                "max_tokens": 64000,
                "system": system,
                "messages": messages,
            },
        },
        "response": {
            "status": 200,
            "headers": {},
            "body": {
                "model": "claude-opus-4-8",
                "content": [{"type": "text", "text": response_text}],
                "usage": usage
                or {
                    "input_tokens": 1200,
                    "output_tokens": 300,
                    "cache_read_input_tokens": 4000,
                    "cache_creation_input_tokens": 800,
                },
            },
        },
    }


def _count_tokens_record(turn):
    record = _main_record(turn, [{"role": "user", "content": "probe"}])
    record["request"]["path"] = "/v1/messages/count_tokens?beta=true"
    record["response"]["body"] = {"input_tokens": 3451}
    return record


def _user(text):
    return {"role": "user", "content": [{"type": "text", "text": text}]}


def _assistant(text):
    return {"role": "assistant", "content": [{"type": "text", "text": text}]}


def _started(minute):
    return datetime(2026, 7, 10, 22, minute, 0, tzinfo=timezone.utc)


def _make_session(store, records, *, minute):
    session_id = store.create_session(client="claude", proxy_mode="reverse", started_at=_started(minute))
    for record in records:
        store.append_record(session_id, record)
    store.finalize_session(session_id, {"api_calls": len(records)})
    return session_id


def _chains(store):
    return find_continuation_chains(store, store.list_session_rows())


def _history():
    """Conversation history shared by the original and the continued session."""
    return [
        _user("Read every file in this repository."),
        _assistant("I read dashboard.html first."),
        _user("Now summarize what you found."),
    ]


def test_prefix_continuation_links_replayed_history(trace_db) -> None:
    store = get_trace_store()
    original = _make_session(
        store,
        [
            _main_record(1, _history()[:1]),
            _main_record(2, _history()),
            _count_tokens_record(3),
        ],
        minute=0,
    )
    replay = _history()
    # Claude Code moves cache markers between replays; matching must ignore them.
    replay[1]["content"][0]["cache_control"] = {"type": "ephemeral"}
    # The in-flight tail message gets extended with tool results on resume.
    replay[2]["content"].append({"type": "text", "text": "Extra tool output replayed on resume."})
    continued = _make_session(
        store,
        [_main_record(1, [*replay, _assistant("Summary of findings."), _user("Continue the plan.")])],
        minute=10,
    )

    chains = _chains(store)

    assert len(chains) == 1
    assert chains[0]["session_ids"] == [original, continued]
    assert chains[0]["links"] == [{"session_id": continued, "link": "prefix"}]


def test_identical_fresh_first_requests_do_not_link(trace_db) -> None:
    """Two fresh sessions with a byte-identical first prompt are not a chain."""
    store = get_trace_store()
    _make_session(store, [_main_record(1, [_user("Hi")])], minute=0)
    _make_session(store, [_main_record(1, [_user("Hi")])], minute=10)

    assert _chains(store) == []


def test_prefix_divergence_beyond_tail_does_not_link(trace_db) -> None:
    store = get_trace_store()
    _make_session(store, [_main_record(1, _history())], minute=0)
    diverged = _history()
    diverged[1] = _assistant("A completely different second turn.")
    _make_session(store, [_main_record(1, [*diverged, _assistant("More."), _user("Go on.")])], minute=10)

    assert _chains(store) == []


def test_different_cwd_does_not_link(trace_db) -> None:
    store = get_trace_store()
    _make_session(store, [_main_record(1, _history())], minute=0)
    _make_session(
        store,
        [_main_record(1, [*_history(), _assistant("Done."), _user("Next.")], system=_SYSTEM_B)],
        minute=10,
    )

    assert _chains(store) == []


def test_compact_resume_links_via_summary_text(trace_db) -> None:
    store = get_trace_store()
    compact_producer = _make_session(
        store,
        [_main_record(1, _history(), response_text=f"<analysis>Reasoning.</analysis>\n{_SUMMARY_TEXT}")],
        minute=0,
    )
    resume_text = (
        f"{COMPACT_RESUME_MARKER} that ran out of context. "
        "The summary below covers the earlier portion of the conversation.\n\n"
        f"Summary:\n{_SUMMARY_TEXT}"
    )
    resumed = _make_session(store, [_main_record(1, [_user(resume_text)])], minute=10)

    chains = _chains(store)

    assert len(chains) == 1
    assert chains[0]["session_ids"] == [compact_producer, resumed]
    assert chains[0]["links"] == [{"session_id": resumed, "link": "compact"}]


def test_resume_marker_without_matching_summary_does_not_link(trace_db) -> None:
    store = get_trace_store()
    _make_session(store, [_main_record(1, _history(), response_text="No summary here.")], minute=0)
    resume_text = f"{COMPACT_RESUME_MARKER} that ran out of context.\n\nSummary:\n{_SUMMARY_TEXT}"
    _make_session(store, [_main_record(1, [_user(resume_text)])], minute=10)

    assert _chains(store) == []


def test_three_session_chain_matches_compact_flow(trace_db) -> None:
    """Original -> `claude -c` replay -> post-compact resume, like the real flow."""
    store = get_trace_store()
    original = _make_session(
        store,
        [_main_record(1, _history()[:1]), _main_record(2, _history())],
        minute=0,
    )
    replay = [*_history(), _assistant("Full replay answer."), _user("Keep going.")]
    continued = _make_session(
        store,
        [_main_record(1, replay, response_text=f"<analysis>Compacting.</analysis>\n{_SUMMARY_TEXT}")],
        minute=10,
    )
    resume_text = (
        f"{COMPACT_RESUME_MARKER} that ran out of context. "
        "The summary below covers the earlier portion of the conversation.\n\n"
        f"Summary:\n{_SUMMARY_TEXT}"
    )
    resumed = _make_session(store, [_main_record(1, [_user(resume_text)])], minute=20)

    chains = _chains(store)

    assert len(chains) == 1
    assert chains[0]["session_ids"] == [original, continued, resumed]
    assert [link["link"] for link in chains[0]["links"]] == ["prefix", "compact"]


@pytest.mark.asyncio
async def test_continuation_chains_endpoint_returns_detected_chains(trace_db) -> None:
    store = get_trace_store()
    original = _make_session(store, [_main_record(1, _history())], minute=0)
    continued = _make_session(
        store,
        [_main_record(1, [*_history(), _assistant("Replay answer."), _user("Continue.")])],
        minute=10,
    )
    _make_session(store, [_main_record(1, [_user("Unrelated session.")])], minute=20)

    server = LiveViewerServer(port=0, dashboard_mode=True)
    port = await server.start()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/api/dashboard/continuation-chains") as resp:
                assert resp.status == 200
                payload = await resp.json()
    finally:
        await server.stop()

    assert payload["max_timeline_sessions"] == MAX_COMPACTION_SESSIONS
    assert payload["scanned_sessions"] == 3
    assert payload["chains"] == [
        {
            "session_ids": [original, continued],
            "links": [{"session_id": continued, "link": "prefix"}],
        }
    ]


@pytest.mark.asyncio
async def test_compaction_route_accepts_chain_length_up_to_cap(trace_db) -> None:
    store = get_trace_store()
    session_ids = [
        _make_session(store, [_main_record(1, [_user(f"Session {index}")])], minute=index)
        for index in range(MAX_COMPACTION_SESSIONS + 1)
    ]

    server = LiveViewerServer(port=0, dashboard_mode=True)
    port = await server.start()
    try:
        async with aiohttp.ClientSession() as session:
            capped = ",".join(session_ids[:MAX_COMPACTION_SESSIONS])
            async with session.get(f"http://127.0.0.1:{port}/dashboard/compaction?sessions={capped}") as resp:
                assert resp.status == 200

            overflowing = ",".join(session_ids)
            async with session.get(f"http://127.0.0.1:{port}/dashboard/compaction?sessions={overflowing}") as resp:
                assert resp.status == 400
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_dashboard_chain_badge_opens_merged_timeline(trace_db) -> None:
    playwright = pytest.importorskip("playwright.async_api")
    store = get_trace_store()
    original = _make_session(store, [_main_record(1, _history())], minute=0)
    continued = _make_session(
        store,
        [_main_record(1, [*_history(), _assistant("Replay answer."), _user("Continue.")])],
        minute=10,
    )

    server = LiveViewerServer(port=0, dashboard_mode=True)
    port = await server.start()
    try:
        async with playwright.async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page = await browser.new_page(viewport={"width": 1440, "height": 1000})
                await page.goto(f"http://127.0.0.1:{port}/dashboard", wait_until="domcontentloaded")

                badges = page.locator(".chain-badge")
                await badges.first.wait_for(state="visible", timeout=8000)
                assert await badges.count() == 2
                badge_texts = await badges.all_inner_texts()
                assert any("1/2" in text for text in badge_texts)
                assert any("2/2" in text for text in badge_texts)
                title = await badges.first.get_attribute("title")
                assert "part" in title and "merged timeline" in title

                await badges.first.click()
                await page.wait_for_selector("#compaction-view:not(.hidden)", timeout=8000)
                assert f"sessions={original},{continued}" in page.url
                await page.wait_for_selector("#compaction-content .cost-tile", timeout=8000)
                subtitle = await page.locator("#compaction-subtitle").inner_text()
                assert original in subtitle and continued in subtitle
            finally:
                await browser.close()
    finally:
        await server.stop()
