"""Tests for compact SQLite trace record storage."""

from __future__ import annotations

import json
import sqlite3
import sys
import types
from copy import deepcopy

import pytest

import claude_tap.trace_store as trace_store_module
from claude_tap.trace_store import (
    BLOB_REF_MARKER,
    COMPACT_RECORD_MARKER,
    SQLITE_BUSY_TIMEOUT_MS,
    SQLITE_MAINTENANCE_WRITES,
    TraceStore,
    get_trace_store,
)


def _large_codex_record(index: int, *, instructions: str, tools: list[dict]) -> dict:
    return {
        "timestamp": f"2026-05-30T04:00:{index:02d}+00:00",
        "turn": index,
        "request": {
            "method": "WEBSOCKET",
            "path": "/v1/responses",
            "body": {
                "model": "gpt-5.5",
                "instructions": instructions,
                "tools": tools,
                "input": [
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": f"round {index}"}],
                    }
                ],
                "previous_response_id": f"resp_{index - 1}" if index > 1 else None,
            },
        },
        "response": {
            "status": 101,
            "body": {
                "id": f"resp_{index}",
                "model": "gpt-5.5",
                "instructions": instructions,
                "tools": tools,
                "output": [
                    {
                        "type": "function_call",
                        "call_id": f"call_{index}",
                        "name": "shell",
                        "arguments": json.dumps({"cmd": f"printf round-{index}"}),
                    },
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": f"done {index}"}],
                    },
                ],
                "usage": {"input_tokens": 100 + index, "output_tokens": 10},
            },
        },
    }


def _raw_record_payloads(db_path) -> list[dict]:
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT payload_json FROM records ORDER BY record_index").fetchall()
    return [json.loads(row[0]) for row in rows]


def test_trace_store_compacts_repeated_instructions_and_tools(trace_db) -> None:
    store = get_trace_store()
    session_id = store.create_session(client="codex", proxy_mode="reverse")
    instructions = "shared system instructions\n" * 800
    tools = [
        {
            "type": "function",
            "name": "shell",
            "description": "shared shell tool description " * 300,
            "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}},
        }
    ]
    records = [_large_codex_record(index, instructions=instructions, tools=tools) for index in range(1, 4)]

    for record in records:
        store.append_record(session_id, deepcopy(record))

    assert store.load_records(session_id) == records
    exported = [json.loads(line) for line in store.export_jsonl(session_id).splitlines()]
    assert exported == records

    raw_payloads = _raw_record_payloads(trace_db)
    assert all(COMPACT_RECORD_MARKER in payload for payload in raw_payloads)
    assert all(BLOB_REF_MARKER in json.dumps(payload) for payload in raw_payloads)
    assert instructions not in json.dumps(raw_payloads, ensure_ascii=False)

    conn = sqlite3.connect(trace_db)
    blob_count = conn.execute("SELECT COUNT(*) FROM record_blobs").fetchone()[0]
    # instructions and tools are shared across request/response and all records.
    assert blob_count == 2


def test_compact_blobs_follow_session_lifecycle(trace_db) -> None:
    store = get_trace_store()
    first_session = store.create_session(client="codex", proxy_mode="reverse")
    second_session = store.create_session(client="codex", proxy_mode="reverse")
    instructions = "shared but session-scoped instructions\n" * 800
    tools = [
        {
            "type": "function",
            "name": "shell",
            "description": "shared shell tool description " * 300,
            "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}},
        }
    ]
    record = _large_codex_record(1, instructions=instructions, tools=tools)

    store.append_record(first_session, deepcopy(record))
    store.append_record(second_session, deepcopy(record))

    conn = store._connect()
    assert conn.execute("SELECT COUNT(*) FROM record_blobs WHERE session_id = ?", (first_session,)).fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM record_blobs WHERE session_id = ?", (second_session,)).fetchone()[0] == 2

    conn.execute("DELETE FROM sessions WHERE id = ?", (first_session,))
    conn.commit()

    assert conn.execute("SELECT COUNT(*) FROM record_blobs WHERE session_id = ?", (first_session,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM record_blobs WHERE session_id = ?", (second_session,)).fetchone()[0] == 2
    assert store.load_records(second_session) == [record]


def test_trace_store_reads_legacy_full_payload_rows(trace_db) -> None:
    store = get_trace_store()
    session_id = store.create_session(client="codex", proxy_mode="reverse")
    legacy_record = {
        "timestamp": "2026-05-30T04:01:00+00:00",
        "turn": 1,
        "request": {"body": {"model": "gpt-5.5", "input": "legacy full row"}},
        "response": {"body": {"output": [{"type": "message", "content": "ok"}]}},
    }
    conn = sqlite3.connect(trace_db)
    conn.execute(
        """
        INSERT INTO records (session_id, record_index, turn, timestamp, payload_json)
        VALUES (?, 1, 1, ?, ?)
        """,
        (
            session_id,
            legacy_record["timestamp"],
            json.dumps(legacy_record, ensure_ascii=False, separators=(",", ":")),
        ),
    )
    conn.commit()

    assert store.load_records(session_id) == [legacy_record]
    assert json.loads(store.export_jsonl(session_id)) == legacy_record


def test_trace_store_rolls_back_failed_append_record_transaction(trace_db, monkeypatch) -> None:
    store = TraceStore(trace_db)
    session_id = store.create_session(client="codex", proxy_mode="reverse")

    def fail_refresh_summary(*args, **kwargs) -> None:
        raise sqlite3.OperationalError("summary update failed")

    monkeypatch.setattr(store, "_refresh_summary_after_append", fail_refresh_summary)

    with pytest.raises(sqlite3.OperationalError, match="summary update failed"):
        store.append_record(session_id, _large_codex_record(1, instructions="rollback", tools=[]))

    conn = store._connect()
    assert not conn.in_transaction
    assert conn.execute("SELECT COUNT(*) FROM records WHERE session_id = ?", (session_id,)).fetchone()[0] == 0
    assert conn.execute("SELECT record_count FROM sessions WHERE id = ?", (session_id,)).fetchone()[0] == 0


def test_finalize_session_merges_storage_error_counters_into_cached_summary(trace_db) -> None:
    store = TraceStore(trace_db)
    session_id = store.create_session(client="codex", proxy_mode="reverse")
    store.append_record(session_id, _large_codex_record(1, instructions="summary", tools=[]))

    store.finalize_session(
        session_id,
        {
            "api_calls": 1,
            "trace_storage_errors": 2,
            "dropped_trace_records": 1,
        },
    )

    row = store.load_session_row(session_id)
    assert row is not None
    summary = json.loads(row["summary_json"])
    assert summary["api_calls"] == 1
    assert summary["trace_storage_errors"] == 2
    assert summary["dropped_trace_records"] == 1


def test_process_write_lock_converts_os_lock_failures_to_sqlite_errors(trace_db, monkeypatch) -> None:
    store = TraceStore(trace_db)

    def fail_lock(lock_file) -> None:
        raise OSError("lock denied")

    monkeypatch.setattr(trace_store_module, "_lock_file_exclusive", fail_lock)

    with pytest.raises(sqlite3.OperationalError, match="trace write lock unavailable"):
        store.create_session(client="codex", proxy_mode="reverse")


def test_trace_store_ignores_checkpoint_errors_after_maintenance_writes(trace_db) -> None:
    class CheckpointLockedConnection:
        def execute(self, statement: str):
            assert statement == "PRAGMA wal_checkpoint(PASSIVE)"
            raise sqlite3.OperationalError("database is locked")

    store = TraceStore(trace_db)
    store._writes_since_maintenance = SQLITE_MAINTENANCE_WRITES - 1

    store._after_write_commit(CheckpointLockedConnection())

    assert store._writes_since_maintenance == 0


def test_windows_file_lock_helpers_use_msvcrt(monkeypatch) -> None:
    calls: list[tuple[str, int, int]] = []
    fake_msvcrt = types.SimpleNamespace(
        LK_LOCK=1,
        LK_UNLCK=2,
        locking=lambda fileno, mode, size: calls.append(("locking", mode, size)),
    )

    class LockFile:
        def __init__(self) -> None:
            self.seek_offsets: list[int] = []

        def seek(self, offset: int) -> None:
            self.seek_offsets.append(offset)

        def fileno(self) -> int:
            return 7

    lock_file = LockFile()
    monkeypatch.setattr(trace_store_module.os, "name", "nt")
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)

    trace_store_module._lock_file_exclusive(lock_file)
    trace_store_module._unlock_file(lock_file)

    assert lock_file.seek_offsets == [0, 0]
    assert calls == [("locking", fake_msvcrt.LK_LOCK, 1), ("locking", fake_msvcrt.LK_UNLCK, 1)]


def test_trace_store_migrates_v3_database_and_keeps_full_rows_readable(tmp_path) -> None:
    db_path = tmp_path / "v3.sqlite3"
    legacy_record = {
        "timestamp": "2026-05-30T04:02:00+00:00",
        "turn": 1,
        "request": {"body": {"model": "gpt-5.5", "input": "v3 row"}},
        "response": {"body": {"output": [{"type": "message", "content": "ok"}]}},
    }
    conn = sqlite3.connect(db_path)
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
            legacy_source_key TEXT NOT NULL DEFAULT '',
            legacy_rel_path TEXT
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
        PRAGMA user_version = 3;
        """
    )
    conn.execute(
        """
        INSERT INTO sessions (id, started_at, updated_at, date_key, client, proxy_mode, status, record_count)
        VALUES ('legacy-session', '2026-05-30T04:02:00+00:00', '2026-05-30T04:02:00+00:00', '2026-05-30', 'codex', 'reverse', 'complete', 1)
        """
    )
    conn.execute(
        """
        INSERT INTO records (session_id, record_index, turn, timestamp, payload_json)
        VALUES ('legacy-session', 1, 1, '2026-05-30T04:02:00+00:00', ?)
        """,
        (json.dumps(legacy_record, ensure_ascii=False, separators=(",", ":")),),
    )
    conn.commit()
    conn.close()

    store = TraceStore(db_path)
    assert store.load_records("legacy-session") == [legacy_record]
    with sqlite3.connect(db_path) as migrated:
        assert migrated.execute("PRAGMA user_version").fetchone()[0] == 4
        assert migrated.execute("SELECT COUNT(*) FROM record_blobs").fetchone()[0] == 0


def test_compact_storage_reduces_large_trace_payload_and_preserves_roundtrip(trace_db) -> None:
    store = get_trace_store()
    session_id = store.create_session(client="codex", proxy_mode="reverse")
    instructions = "repeatable instructions block " * 2000
    tools = [
        {
            "type": "function",
            "name": f"tool_{tool_index}",
            "description": "repeatable tool schema " * 500,
            "parameters": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
        }
        for tool_index in range(4)
    ]
    records = [_large_codex_record(index, instructions=instructions, tools=tools) for index in range(1, 81)]
    raw_jsonl = "".join(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n" for record in records)

    for record in records:
        store.append_record(session_id, deepcopy(record))

    conn = sqlite3.connect(trace_db)
    stored_payload_bytes = conn.execute("SELECT SUM(LENGTH(payload_json)) FROM records").fetchone()[0]
    blob_payload_bytes = conn.execute("SELECT SUM(size_bytes) FROM record_blobs").fetchone()[0]
    compact_total = stored_payload_bytes + blob_payload_bytes

    assert store.load_records(session_id) == records
    assert [json.loads(line) for line in store.export_jsonl(session_id).splitlines()] == records
    assert compact_total < len(raw_jsonl.encode("utf-8")) * 0.15
    assert conn.execute("SELECT COUNT(*) FROM record_blobs").fetchone()[0] == 2


def test_dashboard_style_reads_do_not_keep_trace_store_connection_open(trace_db) -> None:
    writer = TraceStore(trace_db)
    session_id = writer.create_session(client="codex", proxy_mode="forward")
    record = _large_codex_record(
        1,
        instructions="read connection regression instructions",
        tools=[],
    )
    writer.append_record(session_id, deepcopy(record))
    writer.close()

    reader = TraceStore(trace_db)

    assert reader.list_session_rows()[0]["id"] == session_id
    assert getattr(reader._tls, "conn", None) is None

    assert reader.load_records(session_id) == [record]
    assert getattr(reader._tls, "conn", None) is None

    conn = reader._open_connection()
    try:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == SQLITE_BUSY_TIMEOUT_MS
    finally:
        conn.close()
