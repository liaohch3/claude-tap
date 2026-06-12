"""Tests for compact SQLite trace record storage."""

from __future__ import annotations

import json
import sqlite3
from copy import deepcopy

from claude_tap.compact_trace import (
    BLOB_KIND_JSON,
    BLOB_REF_MARKER,
    json_blob_payload,
    load_compact_trace,
    make_blob_ref,
)
from claude_tap.trace_store import (
    COMPACT_RECORD_MARKER,
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


def _large_message_item(index: int) -> dict:
    return {
        "role": "user" if index % 2 else "assistant",
        "content": [
            {
                "type": "input_text",
                "text": f"shared history item {index} " + ("long repeated agent context payload " * 120),
            }
        ],
    }


def _messages_record(index: int, messages: list[dict]) -> dict:
    return {
        "timestamp": f"2026-06-11T08:00:{index:02d}+00:00",
        "turn": index,
        "request": {
            "method": "POST",
            "path": "/v1/messages",
            "body": {
                "model": "claude-sonnet-4-6",
                "messages": deepcopy(messages),
            },
        },
        "response": {
            "status": 200,
            "body": {
                "content": [{"type": "text", "text": f"ok {index}"}],
                "usage": {"input_tokens": 1000 + index, "output_tokens": 12},
            },
        },
    }


def _responses_input_record(index: int, input_items: list[dict]) -> dict:
    return {
        "timestamp": f"2026-06-11T08:01:{index:02d}+00:00",
        "turn": index,
        "request": {
            "method": "POST",
            "path": "/v1/responses",
            "body": {
                "model": "gpt-5.5",
                "input": deepcopy(input_items),
            },
        },
        "response": {
            "status": 200,
            "body": {
                "output": [{"type": "message", "content": [{"type": "output_text", "text": f"done {index}"}]}],
                "usage": {"input_tokens": 900 + index, "output_tokens": 9},
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


def test_trace_store_reads_legacy_compact_rows_without_refs(trace_db) -> None:
    store = get_trace_store()
    session_id = store.create_session(client="codex", proxy_mode="reverse")
    instructions = "legacy shared instructions " * 200
    tools = [
        {
            "type": "function",
            "name": "legacy_shell",
            "description": "legacy shared tool schema " * 120,
            "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}},
        }
    ]
    fake_user_blob_ref = {
        BLOB_REF_MARKER: {
            "version": 1,
            "kind": "json",
            "hash": "sha256:user-controlled-marker-shape",
            "bytes": 42,
        }
    }
    legacy_record = _large_codex_record(1, instructions=instructions, tools=tools)
    legacy_record["response"]["body"]["output"].append(
        {
            "type": "message",
            "content": [
                {
                    "type": "output_text",
                    "text": "preserve marker-shaped user payload",
                    "metadata": fake_user_blob_ref,
                }
            ],
        }
    )
    compact_payload = deepcopy(legacy_record)
    conn = sqlite3.connect(trace_db)
    for value in (instructions, tools):
        payload_json, size_bytes, hash_value = json_blob_payload(value)
        conn.execute(
            """
            INSERT OR IGNORE INTO record_blobs (session_id, hash, kind, payload_json, size_bytes, created_at)
            VALUES (?, ?, ?, ?, ?, '2026-06-11T08:02:00+00:00')
            """,
            (session_id, hash_value, BLOB_KIND_JSON, payload_json, size_bytes),
        )
        ref = make_blob_ref(hash_value, size_bytes)
        for section in ("request", "response"):
            target = compact_payload[section]["body"]
            if target.get("instructions") == value:
                target["instructions"] = ref
            if target.get("tools") == value:
                target["tools"] = ref
    legacy_compact_payload = {
        COMPACT_RECORD_MARKER: {
            "version": 1,
            "encoding": "json-blob-ref",
        },
        "record": compact_payload,
    }
    conn.execute(
        """
        INSERT INTO records (session_id, record_index, turn, timestamp, payload_json)
        VALUES (?, 1, 1, ?, ?)
        """,
        (
            session_id,
            legacy_record["timestamp"],
            json.dumps(legacy_compact_payload, ensure_ascii=False, separators=(",", ":")),
        ),
    )
    conn.commit()

    assert store.load_records(session_id) == [legacy_record]
    assert [json.loads(line) for line in store.export_jsonl(session_id).splitlines()] == [legacy_record]
    assert load_compact_trace(store.export_compact(session_id)) == [legacy_record]


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


def test_trace_store_compacts_repeated_messages_without_prefix_dependency(trace_db) -> None:
    store = get_trace_store()
    session_id = store.create_session(client="claude", proxy_mode="reverse")
    first = _large_message_item(1)
    second = _large_message_item(2)
    third = _large_message_item(3)
    fourth = _large_message_item(4)
    fifth = _large_message_item(5)
    records = [
        _messages_record(1, [first, second]),
        _messages_record(2, [third, first, second]),
        # This is intentionally not an append-only prefix of the previous request.
        _messages_record(3, [fourth, second, first, third]),
        _messages_record(4, [first, fourth, second, third]),
        _messages_record(5, [fifth, first, fourth, second, third]),
    ]
    raw_jsonl = "".join(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n" for record in records)

    for record in records:
        store.append_record(session_id, deepcopy(record))

    assert store.load_records(session_id) == records
    assert store.load_records(session_id, limit=1, offset=1) == [records[1]]
    assert [json.loads(line) for line in store.export_jsonl(session_id).splitlines()] == records

    raw_payloads = _raw_record_payloads(trace_db)
    encoded_payloads = json.dumps(raw_payloads, ensure_ascii=False)
    assert all(COMPACT_RECORD_MARKER in payload for payload in raw_payloads)
    assert BLOB_REF_MARKER in encoded_payloads
    assert "shared history item 1" not in encoded_payloads

    conn = sqlite3.connect(trace_db)
    stored_payload_bytes = conn.execute("SELECT SUM(LENGTH(payload_json)) FROM records").fetchone()[0]
    blob_payload_bytes = conn.execute("SELECT SUM(size_bytes) FROM record_blobs").fetchone()[0]
    compact_total = stored_payload_bytes + blob_payload_bytes

    assert compact_total < len(raw_jsonl.encode("utf-8")) * 0.45
    assert conn.execute("SELECT COUNT(*) FROM record_blobs").fetchone()[0] == 5


def test_trace_store_compacts_repeated_responses_input_items(trace_db) -> None:
    store = get_trace_store()
    session_id = store.create_session(client="codex", proxy_mode="reverse")
    shared_context = _large_message_item(1)
    tool_result = {
        "type": "function_call_output",
        "call_id": "call_shared",
        "output": "large tool result " * 400,
    }
    records = [
        _responses_input_record(1, [shared_context, tool_result]),
        _responses_input_record(2, [shared_context, tool_result, _large_message_item(2)]),
    ]

    for record in records:
        store.append_record(session_id, deepcopy(record))

    assert store.load_records(session_id) == records
    raw_payloads = _raw_record_payloads(trace_db)
    encoded_payloads = json.dumps(raw_payloads, ensure_ascii=False)
    assert BLOB_REF_MARKER in encoded_payloads
    assert "large tool result" not in encoded_payloads

    conn = sqlite3.connect(trace_db)
    assert conn.execute("SELECT COUNT(*) FROM record_blobs").fetchone()[0] == 3


def test_compact_records_preserve_user_blob_ref_shaped_payloads(trace_db) -> None:
    store = get_trace_store()
    session_id = store.create_session(client="codex", proxy_mode="reverse")
    fake_user_blob_ref = {
        BLOB_REF_MARKER: {
            "version": 1,
            "kind": "json",
            "hash": "sha256:user-controlled-marker-shape",
            "bytes": 42,
        }
    }
    shared_context = _large_message_item(1)
    records = [
        _responses_input_record(
            1,
            [
                shared_context,
                {
                    "type": "function_call_output",
                    "call_id": "call_marker",
                    "output": fake_user_blob_ref,
                },
            ],
        ),
        _responses_input_record(
            2,
            [
                shared_context,
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "second turn keeps a user supplied marker-shaped object",
                            "metadata": fake_user_blob_ref,
                        }
                    ],
                },
            ],
        ),
    ]

    for record in records:
        store.append_record(session_id, deepcopy(record))

    raw_payloads = _raw_record_payloads(trace_db)
    assert any(COMPACT_RECORD_MARKER in payload for payload in raw_payloads)
    assert store.load_records(session_id) == records
    assert [json.loads(line) for line in store.export_jsonl(session_id).splitlines()] == records
    assert load_compact_trace(store.export_compact(session_id)) == records


def test_trace_store_keeps_small_messages_inline(trace_db) -> None:
    store = get_trace_store()
    session_id = store.create_session(client="claude", proxy_mode="reverse")
    record = _messages_record(1, [{"role": "user", "content": "hi"}])

    store.append_record(session_id, deepcopy(record))

    assert store.load_records(session_id) == [record]
    raw_payloads = _raw_record_payloads(trace_db)
    assert COMPACT_RECORD_MARKER not in raw_payloads[0]
    assert BLOB_REF_MARKER not in json.dumps(raw_payloads, ensure_ascii=False)

    conn = sqlite3.connect(trace_db)
    assert conn.execute("SELECT COUNT(*) FROM record_blobs").fetchone()[0] == 0


def test_trace_store_message_item_compaction_scales_to_long_reordered_histories(trace_db) -> None:
    store = get_trace_store()
    session_id = store.create_session(client="codex", proxy_mode="reverse")
    shared_items = [_large_message_item(index) for index in range(1, 21)]
    records = []
    for turn in range(1, 61):
        history = [shared_items[(turn + step * 7) % len(shared_items)] for step in range(14)]
        if turn % 3 == 0:
            history = list(reversed(history))
        records.append(_responses_input_record(turn, history))
    raw_jsonl = "".join(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n" for record in records)

    for record in records:
        store.append_record(session_id, deepcopy(record))

    conn = sqlite3.connect(trace_db)
    stored_payload_bytes = conn.execute("SELECT SUM(LENGTH(payload_json)) FROM records").fetchone()[0]
    blob_payload_bytes = conn.execute("SELECT SUM(size_bytes) FROM record_blobs").fetchone()[0]
    compact_total = stored_payload_bytes + blob_payload_bytes

    assert store.load_records(session_id) == records
    assert store.load_records(session_id, limit=3, offset=41) == records[41:44]
    assert [json.loads(line) for line in store.export_jsonl(session_id).splitlines()] == records
    assert load_compact_trace(store.export_compact(session_id)) == records
    assert compact_total < len(raw_jsonl.encode("utf-8")) * 0.2
    assert conn.execute("SELECT COUNT(*) FROM record_blobs").fetchone()[0] == len(shared_items)
