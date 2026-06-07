from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_tap.codex_app_transcript import (
    CODEX_APP_TRANSPORT,
    build_codex_app_transcript_records,
    import_codex_app_transcripts,
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


@pytest.mark.asyncio
async def test_import_codex_app_transcripts_appends_only_new_completed_records(trace_db, tmp_path: Path) -> None:
    transcript = tmp_path / ".codex" / "sessions" / "2026" / "06" / "07" / "rollout.jsonl"
    rows = _session_rows()
    _write_jsonl(transcript, rows[:8])

    from claude_tap.trace_store import get_trace_store

    store = get_trace_store()
    session_id = store.create_session(client="codexapp", proxy_mode="transcript")
    writer = TraceWriter(session_id, store=store, metadata={"client": "codexapp", "proxy_mode": "transcript"})
    state: dict[Path, int] = {}

    imported = await import_codex_app_transcripts(
        writer,
        since=0,
        home=tmp_path,
        state=state,
        include_incomplete=False,
    )
    assert imported == 1

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
    records = store.load_records(session_id)
    assert len(records) == 2
    assert records[0]["capture"] == {"client": "codexapp", "proxy_mode": "transcript"}
    assert records[1]["response"]["body"]["usage"]["output_tokens"] == 4
