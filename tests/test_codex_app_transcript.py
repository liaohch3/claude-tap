from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from claude_tap.codex_app_transcript import (
    CODEX_APP_TRANSPORT,
    CodexAppTranscriptSessionRegistry,
    build_codex_app_transcript_records,
    codex_app_home,
    find_codex_app_transcripts,
    import_codex_app_transcripts,
    import_codex_app_transcripts_to_sessions,
    watch_codex_app_transcripts,
)
from claude_tap.trace import TraceWriter


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")


def _session_rows() -> list[dict]:
    return [
        {
            "type": "session_meta",
            "timestamp": "2026-06-07T01:00:00Z",
            "payload": {
                "id": "session-123",
                "cli_version": "0.1.0",
                "source": "codex-app",
                "cwd": "/tmp/work",
                "base_instructions": {"text": "You are Codex."},
                "dynamic_tools": [
                    {
                        "namespace": "functions",
                        "name": "exec_command",
                        "description": "Run a command.",
                        "inputSchema": {"type": "object"},
                    }
                ],
            },
        },
        {
            "type": "turn_context",
            "timestamp": "2026-06-07T01:00:01Z",
            "payload": {"model": "gpt-5.4-codex", "cwd": "/tmp/work"},
        },
        {
            "type": "response_item",
            "timestamp": "2026-06-07T01:00:02Z",
            "payload": {
                "type": "message",
                "role": "developer",
                "content": [{"type": "input_text", "text": "Follow repo rules."}],
            },
        },
        {
            "type": "response_item",
            "timestamp": "2026-06-07T01:00:03Z",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "inspect workspace"}],
            },
        },
        {
            "type": "response_item",
            "timestamp": "2026-06-07T01:00:04Z",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "I will inspect it."}],
            },
        },
        {
            "type": "response_item",
            "timestamp": "2026-06-07T01:00:05Z",
            "payload": {
                "type": "function_call",
                "name": "functions.exec_command",
                "call_id": "call-1",
                "arguments": '{"cmd":"pwd"}',
            },
        },
        {
            "type": "response_item",
            "timestamp": "2026-06-07T01:00:06Z",
            "payload": {"type": "function_call_output", "call_id": "call-1", "output": "/tmp/work"},
        },
        {
            "type": "event_msg",
            "timestamp": "2026-06-07T01:00:07Z",
            "payload": {
                "type": "token_count",
                "info": {
                    "last_token_usage": {
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "cached_input_tokens": 3,
                        "reasoning_output_tokens": 1,
                        "total_tokens": 15,
                    }
                },
            },
        },
        {
            "type": "response_item",
            "timestamp": "2026-06-07T01:00:08Z",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Workspace is /tmp/work."}],
            },
        },
        {
            "type": "event_msg",
            "timestamp": "2026-06-07T01:00:09Z",
            "payload": {
                "type": "token_count",
                "info": {"last_token_usage": {"input_tokens": 8, "output_tokens": 4, "total_tokens": 12}},
            },
        },
    ]


def test_build_codex_app_transcript_records_preserves_turn_context(tmp_path: Path) -> None:
    transcript = tmp_path / ".codex" / "sessions" / "2026" / "06" / "07" / "rollout.jsonl"
    _write_jsonl(transcript, _session_rows())

    records = build_codex_app_transcript_records(transcript, start_turn=3)

    assert len(records) == 2
    first = records[0]
    assert first["transport"] == CODEX_APP_TRANSPORT
    assert first["turn"] == 3
    assert first["request"]["method"] == "CODEX_APP_TRANSCRIPT"
    assert first["request"]["path"] == "/v1/responses"
    assert first["request"]["headers"]["x-codex-app-session-id"] == "session-123"

    body = first["request"]["body"]
    assert body["model"] == "gpt-5.4-codex"
    assert body["instructions"] == "You are Codex."
    assert body["metadata"] == {
        "codex_app_session_id": "session-123",
        "codex_app_source": "codex-app",
        "cwd": "/tmp/work",
    }
    assert body["tools"][0]["name"] == "functions.exec_command"
    assert [item["role"] for item in body["input"]] == ["developer", "user"]

    output = first["response"]["body"]["output"]
    assert output[0]["role"] == "assistant"
    assert output[1]["type"] == "function_call"
    assert first["response"]["body"]["usage"] == {
        "input_tokens": 10,
        "output_tokens": 5,
        "total_tokens": 15,
        "cache_read_input_tokens": 3,
        "reasoning_output_tokens": 1,
    }

    second_input = records[1]["request"]["body"]["input"]
    assert [item["type"] for item in second_input[-3:]] == ["message", "function_call", "function_call_output"]
    assert records[1]["response"]["body"]["output"][0]["content"][0]["text"] == "Workspace is /tmp/work."


def test_codex_app_home_and_transcript_discovery(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    custom_home = tmp_path / "custom-codex"
    monkeypatch.setenv("CODEX_HOME", str(custom_home))

    assert codex_app_home() == custom_home
    assert codex_app_home(tmp_path) == tmp_path / ".codex"
    assert codex_app_home(tmp_path / ".codex") == tmp_path / ".codex"
    assert find_codex_app_transcripts(since=0, home=tmp_path / "missing") == []

    older = tmp_path / ".codex" / "sessions" / "older.jsonl"
    newer = tmp_path / ".codex" / "sessions" / "nested" / "newer.jsonl"
    _write_jsonl(older, [{"type": "session_meta", "payload": {}}])
    _write_jsonl(newer, [{"type": "session_meta", "payload": {}}])
    os.utime(older, (10, 10))
    os.utime(newer, (20, 20))

    assert find_codex_app_transcripts(since=0, home=tmp_path) == [older, newer]
    assert find_codex_app_transcripts(since=15, home=tmp_path) == [newer]


def test_build_codex_app_transcript_records_tolerates_noisy_rows(tmp_path: Path) -> None:
    missing = tmp_path / ".codex" / "sessions" / "missing.jsonl"
    assert build_codex_app_transcript_records(missing, start_turn=1) == []

    transcript = tmp_path / ".codex" / "sessions" / "noisy.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text(
        "\n".join(
            [
                "",
                "not-json",
                "[]",
                json.dumps({"type": "session_meta", "timestamp": "2026-06-07T01:00:00Z", "payload": []}),
                json.dumps(
                    {
                        "type": "session_meta",
                        "timestamp": "2026-06-07T01:00:01Z",
                        "payload": {
                            "id": "",
                            "originator": "codex-desktop",
                            "base_instructions": " Be helpful. ",
                            "dynamic_tools": [
                                "bad",
                                {},
                                {"name": ""},
                                {"name": "read_file", "input_schema": {"type": "object"}},
                            ],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "response_item",
                        "timestamp": "2026-06-07T01:00:02Z",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "hello"}],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "response_item",
                        "timestamp": "2026-06-07T01:00:03Z",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "hi"}],
                        },
                    }
                ),
                json.dumps(
                    {"type": "event_msg", "timestamp": "2026-06-07T01:00:04Z", "payload": {"type": "token_count"}}
                ),
                json.dumps(
                    {
                        "type": "response_item",
                        "timestamp": "2026-06-07T01:00:05Z",
                        "payload": {
                            "type": "reasoning",
                            "summary": [{"text": "thinking"}],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "event_msg",
                        "timestamp": "2026-06-07T01:00:06Z",
                        "payload": {"type": "token_count", "info": {}},
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    records = build_codex_app_transcript_records(transcript, start_turn=1)

    assert len(records) == 2
    assert records[0]["request"]["body"]["instructions"] == "Be helpful."
    assert records[0]["request"]["body"]["metadata"]["codex_app_source"] == "codex-desktop"
    assert records[0]["request"]["body"]["tools"] == [
        {"type": "function", "name": "read_file", "parameters": {"type": "object"}}
    ]
    assert "usage" not in records[0]["response"]["body"]
    assert records[1]["response"]["body"]["output"][0]["type"] == "reasoning"


@pytest.mark.asyncio
async def test_import_codex_app_transcripts_appends_only_new_completed_records(trace_db, tmp_path: Path) -> None:
    transcript = tmp_path / ".codex" / "sessions" / "2026" / "06" / "07" / "rollout.jsonl"
    rows = _session_rows()
    _write_jsonl(transcript, rows[:8])

    from claude_tap.trace_store import get_trace_store

    store = get_trace_store()
    session_id = store.create_session(client="codexapp", proxy_mode="transcript")
    writer = TraceWriter(session_id, store=store, metadata={"client": "codexapp", "proxy_mode": "transcript"})
    state = {}

    imported = await import_codex_app_transcripts(
        writer,
        since=0,
        home=tmp_path,
        state=state,
        include_incomplete=False,
    )
    assert imported == 1
    first_offset = state[transcript].offset
    assert state[transcript].parser.response_count == 1

    imported = await import_codex_app_transcripts(
        writer,
        since=0,
        home=tmp_path,
        state=state,
        include_incomplete=False,
    )
    assert imported == 0

    _write_jsonl(transcript, rows)
    imported = await import_codex_app_transcripts(
        writer,
        since=0,
        home=tmp_path,
        state=state,
        include_incomplete=False,
    )
    writer.close()

    assert imported == 1
    assert state[transcript].offset > first_offset
    assert state[transcript].parser.response_count == 2
    records = store.load_records(session_id)
    assert len(records) == 2
    assert [record["turn"] for record in records] == [1, 2]
    assert records[0]["capture"] == {"client": "codexapp", "proxy_mode": "transcript"}
    assert records[1]["response"]["body"]["usage"]["output_tokens"] == 4


@pytest.mark.asyncio
async def test_import_codex_app_transcripts_can_append_live_incomplete_records(trace_db, tmp_path: Path) -> None:
    transcript = tmp_path / ".codex" / "sessions" / "2026" / "06" / "07" / "rollout.jsonl"
    rows = _session_rows()
    _write_jsonl(transcript, rows[:5])

    from claude_tap.trace_store import get_trace_store

    store = get_trace_store()
    session_id = store.create_session(client="codexapp", proxy_mode="transcript")
    writer = TraceWriter(session_id, store=store, metadata={"client": "codexapp", "proxy_mode": "transcript"})
    state = {}

    imported = await import_codex_app_transcripts(
        writer,
        since=0,
        home=tmp_path,
        state=state,
        include_incomplete=True,
    )
    assert imported == 1

    imported = await import_codex_app_transcripts(
        writer,
        since=0,
        home=tmp_path,
        state=state,
        include_incomplete=True,
    )
    assert imported == 0

    _write_jsonl(transcript, rows[:8])
    imported = await import_codex_app_transcripts(
        writer,
        since=0,
        home=tmp_path,
        state=state,
        include_incomplete=True,
    )
    writer.close()

    assert imported == 1
    records = store.load_records(session_id)
    assert len(records) == 2
    assert records[0]["response"]["body"]["status"] == "in_progress"
    assert records[0]["capture"] == {
        "client": "codexapp",
        "proxy_mode": "transcript",
        "codex_app_partial": True,
    }
    assert records[0]["response"]["body"]["output"][0]["content"][0]["text"] == "I will inspect it."
    assert records[1]["response"]["body"]["status"] == "completed"
    assert records[1]["response"]["body"]["usage"]["output_tokens"] == 5


@pytest.mark.asyncio
async def test_import_codex_app_transcripts_resets_state_when_file_shrinks(trace_db, tmp_path: Path) -> None:
    transcript = tmp_path / ".codex" / "sessions" / "2026" / "06" / "07" / "rollout.jsonl"
    _write_jsonl(transcript, _session_rows()[:8])

    from claude_tap.trace_store import get_trace_store

    store = get_trace_store()
    session_id = store.create_session(client="codexapp", proxy_mode="transcript")
    writer = TraceWriter(session_id, store=store, metadata={"client": "codexapp", "proxy_mode": "transcript"})
    state = {transcript: 99}

    imported = await import_codex_app_transcripts(
        writer,
        since=0,
        home=tmp_path,
        state=state,
        include_incomplete=False,
    )
    writer.close()

    assert imported == 1
    assert state[transcript].parser.response_count == 1
    assert len(store.load_records(session_id)) == 1


@pytest.mark.asyncio
async def test_import_codex_app_transcripts_to_sessions_splits_codex_queries(trace_db, tmp_path: Path) -> None:
    first = tmp_path / ".codex" / "sessions" / "2026" / "06" / "07" / "first.jsonl"
    second = tmp_path / ".codex" / "sessions" / "2026" / "06" / "07" / "second.jsonl"
    first_rows = _session_rows()
    second_rows = _session_rows()
    first_rows[0]["payload"]["id"] = "codex-query-alpha"
    first_rows[3]["payload"]["content"][0]["text"] = "write runtime wiki"
    second_rows[0]["payload"]["id"] = "codex-query-beta"
    second_rows[3]["payload"]["content"][0]["text"] = "add Codex App listener"
    _write_jsonl(first, first_rows[:8])
    _write_jsonl(second, second_rows[:8])

    from claude_tap.trace_store import SessionQuery, get_trace_store

    store = get_trace_store()
    registry = CodexAppTranscriptSessionRegistry(
        store=store,
        metadata={"client": "codexapp", "proxy_mode": "transcript"},
    )

    imported = await import_codex_app_transcripts_to_sessions(
        registry,
        since=0,
        home=tmp_path,
        include_incomplete=False,
    )
    registry.close()

    assert imported == 2
    rows = store.list_session_rows(query=SessionQuery(agent_clients=("codexapp",)))
    assert len(rows) == 2
    records_by_session = {row["id"]: store.load_records(row["id"]) for row in rows}
    assert sorted(len(records) for records in records_by_session.values()) == [1, 1]
    prompts = sorted(
        records[0]["request"]["body"]["input"][1]["content"][0]["text"] for records in records_by_session.values()
    )
    assert prompts == ["add Codex App listener", "write runtime wiki"]
    app_session_ids = sorted(
        records[0]["request"]["body"]["metadata"]["codex_app_session_id"] for records in records_by_session.values()
    )
    assert app_session_ids == ["codex-query-alpha", "codex-query-beta"]
    capture_app_session_ids = sorted(
        records[0]["capture"]["codex_app_session_id"] for records in records_by_session.values()
    )
    assert capture_app_session_ids == ["codex-query-alpha", "codex-query-beta"]


@pytest.mark.asyncio
async def test_watch_codex_app_transcripts_flushes_incomplete_record_on_cancel(trace_db, tmp_path: Path) -> None:
    transcript = tmp_path / ".codex" / "sessions" / "2026" / "06" / "07" / "rollout.jsonl"
    rows = _session_rows()[:5]
    _write_jsonl(transcript, rows)

    from claude_tap.trace_store import get_trace_store

    store = get_trace_store()
    session_id = store.create_session(client="codexapp", proxy_mode="transcript")
    writer = TraceWriter(session_id, store=store, metadata={"client": "codexapp", "proxy_mode": "transcript"})

    task = asyncio.create_task(
        watch_codex_app_transcripts(
            writer,
            since=0,
            home=tmp_path,
            poll_interval=0.01,
        )
    )
    await asyncio.sleep(0.03)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    writer.close()

    records = store.load_records(session_id)
    assert len(records) == 1
    assert records[0]["response"]["body"]["status"] == "in_progress"
    assert records[0]["response"]["body"]["output"][0]["content"][0]["text"] == "I will inspect it."
