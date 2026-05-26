from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from claude_tap.history import cleanup_trace_sessions, delete_trace_history, migrate_legacy_traces
from claude_tap.trace_store import TraceStore, get_trace_store, reset_trace_store


def _write_legacy_session(base: Path, stem: str, *, date: str = "2026-05-01") -> Path:
    date_dir = base / date if date != "legacy" else base
    date_dir.mkdir(parents=True, exist_ok=True)
    jsonl = date_dir / f"{stem}.jsonl"
    jsonl.write_text(
        json.dumps({"request_id": stem, "turn": 1, "request": {}, "response": {}}) + "\n", encoding="utf-8"
    )
    (date_dir / f"{stem}.log").write_text("10:00:00 proxy log", encoding="utf-8")
    return jsonl


def _write_v2_database(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                date_key TEXT NOT NULL,
                client TEXT NOT NULL DEFAULT '',
                proxy_mode TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                record_count INTEGER NOT NULL DEFAULT 0,
                summary_json TEXT,
                legacy_rel_path TEXT UNIQUE
            );
            CREATE TABLE records (
                session_id TEXT NOT NULL,
                record_index INTEGER NOT NULL,
                turn INTEGER,
                timestamp TEXT,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (session_id, record_index)
            );
            CREATE TABLE proxy_logs (
                session_id TEXT NOT NULL,
                line_no INTEGER NOT NULL,
                logged_at TEXT,
                level TEXT,
                message TEXT NOT NULL,
                PRIMARY KEY (session_id, line_no)
            );
            CREATE TABLE migration_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT INTO sessions (
                id, started_at, updated_at, date_key, client, proxy_mode,
                status, record_count, summary_json, legacy_rel_path
            )
            VALUES (
                'old-session', '2026-05-01T12:00:00+00:00', '2026-05-01T12:00:00+00:00',
                '2026-05-01', 'claude', 'reverse', 'complete', 1, NULL, '2026-05-01/trace_same.jsonl'
            );
            INSERT INTO records (session_id, record_index, turn, timestamp, payload_json)
            VALUES ('old-session', 1, 1, '2026-05-01T12:00:00+00:00', '{"turn":1}');
            INSERT INTO proxy_logs (session_id, line_no, logged_at, level, message)
            VALUES ('old-session', 1, '12:00:00', 'INFO', 'legacy log');
            PRAGMA user_version = 2;
            """
        )


def test_migrate_legacy_directory_imports_jsonl_and_logs(trace_db, tmp_path: Path) -> None:
    _write_legacy_session(tmp_path, "trace_old")
    imported = migrate_legacy_traces(tmp_path)

    assert imported == 1
    sessions = get_trace_store().list_session_rows()
    assert len(sessions) == 1
    assert sessions[0]["legacy_rel_path"] == "2026-05-01/trace_old.jsonl"
    assert get_trace_store().export_log(sessions[0]["id"]).startswith("10:00:00")


def test_migrate_legacy_directory_dedupes_per_output_dir(trace_db, tmp_path: Path) -> None:
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    _write_legacy_session(project_a, "trace_same")
    _write_legacy_session(project_b, "trace_same")

    assert migrate_legacy_traces(project_a) == 1
    assert migrate_legacy_traces(project_a) == 0
    assert migrate_legacy_traces(project_b) == 1

    sessions = get_trace_store().list_session_rows()
    assert len(sessions) == 2
    assert [row["legacy_rel_path"] for row in sessions].count("2026-05-01/trace_same.jsonl") == 2


def test_migrate_legacy_directory_treats_duplicate_insert_as_already_imported(
    trace_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_legacy_session(tmp_path, "trace_same")
    store = get_trace_store()
    monkeypatch.setattr(store, "_legacy_session_exists", lambda _source, _rel_path: False)

    assert store.migrate_legacy_directory(tmp_path) == 1
    assert store.migrate_legacy_directory(tmp_path) == 0

    sessions = get_trace_store().list_session_rows()
    assert len(sessions) == 1
    assert sessions[0]["legacy_rel_path"] == "2026-05-01/trace_same.jsonl"


def test_migrate_legacy_directory_upgrades_v2_schema_for_source_key_dedupe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "v2.sqlite3"
    _write_v2_database(db_path)
    monkeypatch.setenv("CLOUDTAP_DB", str(db_path))
    reset_trace_store()
    project = tmp_path / "project"
    _write_legacy_session(project, "trace_same")

    assert migrate_legacy_traces(project) == 1

    sessions = get_trace_store().list_session_rows()
    assert len(sessions) == 2
    assert [row["legacy_rel_path"] for row in sessions].count("2026-05-01/trace_same.jsonl") == 2
    conn = get_trace_store()._connect()
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.execute("DELETE FROM sessions WHERE id = 'old-session'")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM records WHERE session_id = 'old-session'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM proxy_logs WHERE session_id = 'old-session'").fetchone()[0] == 0


def test_v2_schema_migration_rolls_back_on_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "v2-failure.sqlite3"
    _write_v2_database(db_path)
    store = TraceStore(db_path)

    def fail_indexes(_conn: sqlite3.Connection) -> None:
        raise RuntimeError("index failure")

    monkeypatch.setattr(store, "_create_v3_indexes", fail_indexes)

    with pytest.raises(RuntimeError, match="index failure"):
        store.list_session_rows()

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"sessions", "records", "proxy_logs"} <= tables
        assert not any(name.startswith("sessions_v2_") for name in tables)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
        assert "legacy_source_key" not in columns


def test_migrate_legacy_directory_reads_manifest_once(
    trace_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_legacy_session(tmp_path, "trace_one")
    _write_legacy_session(tmp_path, "trace_two")
    (tmp_path / ".cloudtap-manifest.json").write_text(
        json.dumps(
            {
                "traces": [
                    {"files": ["2026-05-01/trace_one.jsonl"], "client": "claude"},
                    {"files": ["2026-05-01/trace_two.jsonl"], "client": "codex"},
                ]
            }
        ),
        encoding="utf-8",
    )
    original_read_text = Path.read_text
    manifest_reads: list[Path] = []

    def counted_read_text(path: Path, *args: object, **kwargs: object) -> str:
        if path.name == ".cloudtap-manifest.json":
            manifest_reads.append(path)
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counted_read_text)

    assert migrate_legacy_traces(tmp_path) == 2
    assert len(manifest_reads) == 1


def test_append_log_refreshes_active_session_heartbeat(trace_db) -> None:
    store = get_trace_store()
    session_id = store.create_session(client="claude", proxy_mode="reverse")
    stale = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    conn = store._connect()
    conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (stale, session_id))
    conn.commit()

    store.append_log(session_id, "proxy still alive", logged_at="12:00:00")

    row = store.load_session_row(session_id)
    assert row is not None
    assert row["updated_at"] > stale


def test_finalize_session_refreshes_cached_summary_timestamp(trace_db) -> None:
    store = get_trace_store()
    session_id = store.create_session(client="claude", proxy_mode="reverse")
    old_timestamp = "2026-05-01T12:00:00+00:00"
    store.store_summary(session_id, {"id": session_id, "status": "active", "updated_at": old_timestamp})

    store.finalize_session(session_id, {"api_calls": 0})

    row = store.load_session_row(session_id)
    assert row is not None
    summary = json.loads(row["summary_json"])
    assert summary["updated_at"] == row["updated_at"]
    assert summary["updated_at"] != old_timestamp


def test_session_rows_sort_by_normalized_updated_at(trace_db) -> None:
    store = get_trace_store()
    older = store.create_session(started_at=datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc))
    newer = store.create_session(started_at=datetime(2026, 5, 1, 11, 0, tzinfo=timezone.utc))
    conn = store._connect()
    conn.execute("UPDATE sessions SET updated_at = '2026-05-01T10:00:00+09:00' WHERE id = ?", (older,))
    conn.execute("UPDATE sessions SET updated_at = '2026-05-01T02:30:00+00:00' WHERE id = ?", (newer,))
    conn.commit()

    assert [row["id"] for row in store.list_session_rows()][:2] == [newer, older]


def test_cleanup_trace_sessions_prunes_by_normalized_started_at(trace_db) -> None:
    store = get_trace_store()
    oldest = store.create_session(started_at=datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc))
    middle = store.create_session(started_at=datetime(2026, 5, 1, 11, 0, tzinfo=timezone.utc))
    newest = store.create_session(started_at=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc))
    for session_id in (oldest, middle, newest):
        store.finalize_session(session_id, {"api_calls": 1})
    conn = store._connect()
    conn.execute("UPDATE sessions SET started_at = '2026-05-01T10:00:00+09:00' WHERE id = ?", (oldest,))
    conn.execute("UPDATE sessions SET started_at = '2026-05-01T02:30:00+00:00' WHERE id = ?", (middle,))
    conn.execute("UPDATE sessions SET started_at = '2026-05-01T03:00:00+00:00' WHERE id = ?", (newest,))
    conn.commit()

    assert cleanup_trace_sessions(2) == 1
    assert {row["id"] for row in store.list_session_rows()} == {middle, newest}


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
    assert result["skipped_sessions"] == 1
    assert result["skipped_files"] == 1
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


def test_cleanup_trace_sessions_skips_protected_and_continues(trace_db) -> None:
    store = get_trace_store()
    session_ids = [
        store.create_session(
            client="claude",
            proxy_mode="reverse",
            started_at=datetime(2026, 5, 1, 12, index, tzinfo=timezone.utc),
        )
        for index in range(5)
    ]
    for session_id in session_ids:
        store.finalize_session(session_id, {"api_calls": 1})

    removed = cleanup_trace_sessions(2, protected_session_id=session_ids[0])

    assert removed == 3
    remaining = {row["id"] for row in store.list_session_rows()}
    assert remaining == {session_ids[0], session_ids[-1]}


def test_cleanup_trace_sessions_skips_active_sessions(trace_db) -> None:
    store = get_trace_store()
    now = datetime.now(timezone.utc)
    session_ids = [
        store.create_session(
            client="claude",
            proxy_mode="reverse",
            started_at=now.replace(minute=index, second=0, microsecond=0),
        )
        for index in range(4)
    ]
    for session_id in (session_ids[0], session_ids[2], session_ids[3]):
        store.finalize_session(session_id, {"api_calls": 1})

    removed = cleanup_trace_sessions(2)

    assert removed == 2
    rows = {row["id"]: row["status"] for row in store.list_session_rows()}
    assert rows == {session_ids[1]: "active", session_ids[3]: "complete"}


def test_cleanup_trace_sessions_removes_stale_active_sessions(trace_db) -> None:
    store = get_trace_store()
    session_ids = [
        store.create_session(
            client="claude",
            proxy_mode="reverse",
            started_at=datetime(2026, 5, 1, 12, index, tzinfo=timezone.utc),
        )
        for index in range(4)
    ]

    removed = cleanup_trace_sessions(2)

    assert removed == 2
    remaining = {row["id"] for row in store.list_session_rows()}
    assert remaining == {session_ids[2], session_ids[3]}


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


@pytest.mark.asyncio
async def test_shared_dashboard_delete_history_requires_force_for_active_sessions(
    trace_db,
    tmp_path: Path,
) -> None:
    import aiohttp

    from claude_tap import LiveViewerServer

    _write_legacy_session(tmp_path, "trace_delete_me", date="2026-05-01")
    migrate_legacy_traces(tmp_path)
    active_session = get_trace_store().create_session(
        client="claude",
        proxy_mode="reverse",
        started_at=datetime(2026, 5, 1, 12, 30, tzinfo=timezone.utc),
    )

    server = LiveViewerServer(port=0, migrate_from=tmp_path, dashboard_mode=True)
    port = await server.start()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.delete(f"http://127.0.0.1:{port}/api/traces/2026-05-01") as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert payload["deleted_sessions"] == 1
                assert payload["skipped_sessions"] == 1

            remaining = {row["id"] for row in get_trace_store().list_session_rows()}
            assert active_session in remaining

            async with session.delete(f"http://127.0.0.1:{port}/api/traces/2026-05-01?force=1") as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert payload["deleted_sessions"] == 1
                assert payload["skipped_sessions"] == 0

            remaining = {row["id"] for row in get_trace_store().list_session_rows()}
            assert active_session not in remaining
    finally:
        await server.stop()
