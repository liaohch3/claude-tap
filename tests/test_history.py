from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_tap.history import cleanup_trace_sessions, delete_trace_history, migrate_legacy_traces
from claude_tap.trace_store import get_trace_store


def _write_legacy_session(base: Path, stem: str, *, date: str = "2026-05-01") -> Path:
    date_dir = base / date if date != "legacy" else base
    date_dir.mkdir(parents=True, exist_ok=True)
    jsonl = date_dir / f"{stem}.jsonl"
    jsonl.write_text(
        json.dumps({"request_id": stem, "turn": 1, "request": {}, "response": {}}) + "\n", encoding="utf-8"
    )
    (date_dir / f"{stem}.log").write_text("10:00:00 proxy log", encoding="utf-8")
    return jsonl


def test_migrate_legacy_directory_imports_jsonl_and_logs(trace_db, tmp_path: Path) -> None:
    _write_legacy_session(tmp_path, "trace_old")
    imported = migrate_legacy_traces(tmp_path)

    assert imported == 1
    sessions = get_trace_store().list_session_rows()
    assert len(sessions) == 1
    assert sessions[0]["legacy_rel_path"] == "2026-05-01/trace_old.jsonl"
    assert get_trace_store().export_log(sessions[0]["id"]).startswith("10:00:00")


def test_delete_trace_history_removes_selected_date_sessions(trace_db, tmp_path: Path) -> None:
    _write_legacy_session(tmp_path, "trace_old", date="2026-05-01")
    _write_legacy_session(tmp_path, "trace_active", date="2026-05-01")
    _write_legacy_session(tmp_path, "trace_other", date="2026-05-02")
    migrate_legacy_traces(tmp_path)

    sessions = {row["legacy_rel_path"]: row["id"] for row in get_trace_store().list_session_rows()}
    protected = {sessions["2026-05-01/trace_active.jsonl"]}

    result = delete_trace_history("2026-05-01", protected_session_ids=protected)

    assert result["deleted_sessions"] == 1
    assert result["deleted_files"] == 1
    remaining = {row["legacy_rel_path"] for row in get_trace_store().list_session_rows()}
    assert "2026-05-01/trace_old.jsonl" not in remaining
    assert "2026-05-01/trace_active.jsonl" in remaining
    assert "2026-05-02/trace_other.jsonl" in remaining


def test_cleanup_trace_sessions_keeps_newest(trace_db, tmp_path: Path) -> None:
    for index in range(4):
        _write_legacy_session(tmp_path, f"trace_{index:02d}", date="2026-05-01")
    migrate_legacy_traces(tmp_path)

    removed = cleanup_trace_sessions(2)

    assert removed == 2
    assert len(get_trace_store().list_session_rows()) == 2


@pytest.mark.asyncio
async def test_live_viewer_delete_history_endpoint(trace_db, tmp_path: Path) -> None:
    import aiohttp

    from claude_tap import LiveViewerServer

    _write_legacy_session(tmp_path, "trace_delete_me", date="2026-05-01")
    migrate_legacy_traces(tmp_path)
    active_session = get_trace_store().create_session(client="claude", proxy_mode="reverse")

    server = LiveViewerServer(session_id=active_session, port=0, migrate_from=tmp_path, dashboard_mode=True)
    port = await server.start()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.delete(f"http://127.0.0.1:{port}/api/traces/2026-05-01") as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert payload["deleted_sessions"] == 1
                assert payload["deleted_files"] == 1

            async with session.get(f"http://127.0.0.1:{port}/api/traces/2026-05-01") as resp:
                assert resp.status == 200
                assert await resp.json() == []

            async with session.delete(f"http://127.0.0.1:{port}/api/traces/not-a-date") as resp:
                assert resp.status == 400
    finally:
        await server.stop()
