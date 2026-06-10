"""Tests for compact SQLite trace record storage."""

from __future__ import annotations

import json
import sqlite3
from copy import deepcopy

from claude_tap.trace_store import (
    BLOB_REF_MARKER,
    COMPACT_RECORD_MARKER,
    PREFIX_DELTA_MARKER,
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


def _history_item(index: int) -> dict:
    return {
        "role": "user" if index % 2 else "assistant",
        "content": [
            {
                "type": "input_text",
                "text": f"history item {index} " + ("shared context payload " * 80),
            }
        ],
    }


def _history_record(index: int, history: list[dict]) -> dict:
    return {
        "timestamp": f"2026-05-30T05:{index:02d}:00+00:00",
        "turn": index,
        "request": {
            "method": "POST",
            "path": "/v1/responses",
            "body": {
                "model": "gpt-5.5",
                "input": deepcopy(history),
            },
        },
        "response": {
            "status": 200,
            "body": {
                "id": f"resp_{index}",
                "output": [{"type": "message", "content": f"ok {index}"}],
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
        assert migrated.execute("PRAGMA user_version").fetchone()[0] == 5
        assert migrated.execute("SELECT COUNT(*) FROM record_blobs").fetchone()[0] == 0


def test_trace_store_migrates_v4_database_and_keeps_full_rows_readable(tmp_path) -> None:
    db_path = tmp_path / "v4.sqlite3"
    legacy_record = {
        "timestamp": "2026-05-30T04:03:00+00:00",
        "turn": 1,
        "request": {"body": {"model": "gpt-5.5", "input": "v4 row"}},
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
        CREATE TABLE record_blobs (
            session_id TEXT NOT NULL,
            hash TEXT NOT NULL,
            kind TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (session_id, hash)
        );
        PRAGMA user_version = 4;
        """
    )
    conn.execute(
        """
        INSERT INTO sessions (id, started_at, updated_at, date_key, client, proxy_mode, status, record_count)
        VALUES ('v4-session', '2026-05-30T04:03:00+00:00', '2026-05-30T04:03:00+00:00', '2026-05-30', 'codex', 'reverse', 'complete', 1)
        """
    )
    conn.execute(
        """
        INSERT INTO records (session_id, record_index, turn, timestamp, payload_json)
        VALUES ('v4-session', 1, 1, '2026-05-30T04:03:00+00:00', ?)
        """,
        (json.dumps(legacy_record, ensure_ascii=False, separators=(",", ":")),),
    )
    conn.commit()
    conn.close()

    store = TraceStore(db_path)
    assert store.load_records("v4-session") == [legacy_record]
    assert json.loads(store.export_jsonl("v4-session")) == legacy_record
    with sqlite3.connect(db_path) as migrated:
        assert migrated.execute("PRAGMA user_version").fetchone()[0] == 5


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


def test_trace_store_prefix_compacts_repeated_request_history_and_preserves_roundtrip(trace_db) -> None:
    store = get_trace_store()
    session_id = store.create_session(client="codex", proxy_mode="reverse")
    history = [_history_item(1), _history_item(2)]
    records = []
    for index in range(1, 41):
        history.append(_history_item(index + 2))
        records.append(_history_record(index, history))

    raw_jsonl = "".join(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n" for record in records)
    for record in records:
        store.append_record(session_id, deepcopy(record))

    assert store.load_records(session_id) == records
    assert store.load_records(session_id, limit=3, offset=27) == records[27:30]
    assert store.load_boundary_records(session_id) == [records[0], records[-1]]
    assert [json.loads(line) for line in store.export_jsonl(session_id).splitlines()] == records
    assert len(json.loads(store.export_compact(session_id))["records"]) == len(records)

    raw_payloads = _raw_record_payloads(trace_db)
    assert PREFIX_DELTA_MARKER not in json.dumps(raw_payloads[0], ensure_ascii=False)
    assert PREFIX_DELTA_MARKER in json.dumps(raw_payloads[1], ensure_ascii=False)
    assert PREFIX_DELTA_MARKER not in json.dumps(raw_payloads[25], ensure_ascii=False)
    assert PREFIX_DELTA_MARKER in json.dumps(raw_payloads[26], ensure_ascii=False)
    assert "history item 1" not in json.dumps(raw_payloads[1], ensure_ascii=False)

    conn = sqlite3.connect(trace_db)
    stored_payload_bytes = conn.execute("SELECT SUM(LENGTH(payload_json)) FROM records").fetchone()[0]
    assert stored_payload_bytes < len(raw_jsonl.encode("utf-8")) * 0.45
