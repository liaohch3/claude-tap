import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import pytest
from aiohttp.test_utils import make_mocked_request

from claude_tap.dashboard import (
    _clean_user_prompt_text,
    _content_text,
    _event_payload,
    _first_error,
    _infer_agent,
    _input_user_text,
    _parts_text,
    _preview,
    _record_host,
    _record_model,
    _record_response_text,
    _record_usage,
    _request_user_text,
    _response_events,
    _response_text,
    dashboard_trace_snapshot,
    list_trace_agents,
    list_trace_sessions,
    load_trace_session,
    read_dashboard_template,
)
from claude_tap.history import migrate_legacy_traces
from claude_tap.live import LiveViewerServer, _record_limit_from_request
from claude_tap.trace import TraceWriter
from claude_tap.trace_log_handler import SQLiteLogHandler
from claude_tap.trace_store import get_trace_store


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False, separators=(",", ":")) for record in records) + "\n",
        encoding="utf-8",
    )


def test_record_limit_from_request_preserves_large_loaded_windows() -> None:
    request = make_mocked_request("GET", "/api/sessions/example/records?limit=1500")

    assert _record_limit_from_request(request) == 1500


def test_dashboard_lists_sessions_by_normalized_updated_at(trace_db) -> None:
    store = get_trace_store()
    older = store.create_session(started_at=datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc))
    newer = store.create_session(started_at=datetime(2026, 5, 1, 11, 0, tzinfo=timezone.utc))
    conn = store._connect()
    conn.execute("UPDATE sessions SET updated_at = '2026-05-01T10:00:00+09:00' WHERE id = ?", (older,))
    conn.execute("UPDATE sessions SET updated_at = '2026-05-01T02:30:00+00:00' WHERE id = ?", (newer,))
    conn.commit()

    assert [session["id"] for session in list_trace_sessions()][:2] == [newer, older]


def _anthropic_record(turn: int = 1) -> dict:
    return {
        "timestamp": "2026-05-20T08:00:00+00:00",
        "request_id": "req_claude",
        "turn": turn,
        "duration_ms": 1200,
        "capture": {"client": "claude", "proxy_mode": "reverse"},
        "request": {
            "method": "POST",
            "path": "/v1/messages",
            "headers": {"Host": "api.anthropic.com"},
            "body": {
                "model": "claude-sonnet-4-6",
                "messages": [{"role": "user", "content": "Explain this repository"}],
            },
        },
        "response": {
            "status": 200,
            "headers": {},
            "body": {
                "model": "claude-sonnet-4-6",
                "content": [{"type": "text", "text": "This is a trace viewer."}],
                "usage": {"input_tokens": 42, "output_tokens": 9},
            },
        },
    }


def _antigravity_record() -> dict:
    return {
        "timestamp": "2026-05-20T09:00:00+00:00",
        "request_id": "req_agy",
        "turn": 1,
        "duration_ms": 900,
        "request": {
            "method": "POST",
            "path": "/v1internal:streamGenerateContent?alt=sse",
            "headers": {"Host": "antigravity-unleash.goog"},
            "body": {
                "request": {
                    "contents": [{"role": "user", "parts": [{"text": "What model are you?"}]}],
                }
            },
        },
        "response": {
            "status": 200,
            "headers": {},
            "body": {
                "candidates": [{"content": {"parts": [{"text": "I am Sonnet."}]}}],
                "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 5},
            },
        },
    }


def _seed_legacy(tmp_path: Path) -> None:
    migrate_legacy_traces(tmp_path)


def test_dashboard_lists_sessions_across_agents(trace_db, tmp_path: Path) -> None:
    claude_trace = tmp_path / "2026-05-20" / "trace_080000.jsonl"
    agy_trace = tmp_path / "2026-05-20" / "trace_090000.jsonl"
    _write_jsonl(claude_trace, [_anthropic_record()])
    _write_jsonl(agy_trace, [_antigravity_record()])

    _seed_legacy(tmp_path)

    sessions = list_trace_sessions()

    assert [session["agent"] for session in sessions] == ["Antigravity", "Claude Code"]
    assert sessions[0]["first_user"] == "What model are you?"
    assert sessions[0]["last_response"] == "I am Sonnet."
    assert sessions[1]["input_tokens"] == 42
    assert sessions[1]["output_tokens"] == 9

    agents = list_trace_agents()
    assert [(agent["label"], agent["sessions"]) for agent in agents] == [("Antigravity", 1), ("Claude Code", 1)]


def test_dashboard_indexes_sessions_in_sqlite(trace_db, tmp_path: Path) -> None:
    trace_path = tmp_path / "2026-05-20" / "trace_080000.jsonl"
    _write_jsonl(trace_path, [_anthropic_record()])
    _seed_legacy(tmp_path)

    sessions = list_trace_sessions()

    assert trace_db.exists()
    assert sessions[0]["record_count"] == 1
    assert sessions[0]["first_user"] == "Explain this repository"

    second_path = tmp_path / "2026-05-20" / "trace_081500.jsonl"
    _write_jsonl(second_path, [_anthropic_record(turn=2)])
    migrate_legacy_traces(tmp_path)
    sessions = list_trace_sessions()
    first_session = next(item for item in sessions if item["legacy_rel_path"] == "2026-05-20/trace_080000.jsonl")
    payload = load_trace_session(first_session["id"])

    assert len(sessions) == 2
    assert payload is not None
    assert payload["records"][0]["turn"] == 1


def test_dashboard_load_session_can_page_sqlite_records(trace_db, tmp_path: Path) -> None:
    trace_path = tmp_path / "2026-05-20" / "trace_080000.jsonl"
    _write_jsonl(trace_path, [_anthropic_record(), _anthropic_record(turn=2), _anthropic_record(turn=3)])
    _seed_legacy(tmp_path)
    session_id = list_trace_sessions()[0]["id"]

    payload = load_trace_session(session_id, record_limit=1, record_offset=1)

    assert payload is not None
    assert payload["session"]["record_count"] == 3
    assert [record["turn"] for record in payload["records"]] == [2]


def test_dashboard_detail_reads_from_sqlite(trace_db, tmp_path: Path) -> None:
    trace_path = tmp_path / "2026-05-20" / "trace_080000.jsonl"
    _write_jsonl(trace_path, [_anthropic_record()])
    _seed_legacy(tmp_path)
    session_id = list_trace_sessions()[0]["id"]

    payload = load_trace_session(session_id)

    assert payload is not None
    assert payload["records"][0]["request_id"] == "req_claude"


def test_dashboard_first_message_uses_first_user_prompt(trace_db, tmp_path: Path) -> None:
    trace_path = tmp_path / "2026-05-20" / "trace_100000.jsonl"
    _write_jsonl(
        trace_path,
        [
            {
                "timestamp": "2026-05-20T10:00:00+00:00",
                "turn": 1,
                "request": {
                    "method": "POST",
                    "path": "/v1/responses",
                    "body": {
                        "model": "gpt-5.5",
                        "input": [
                            {"role": "developer", "content": [{"type": "input_text", "text": "developer setup"}]},
                            {"type": "function_call_output", "output": "tool result"},
                            {
                                "role": "user",
                                "content": [{"type": "input_text", "text": "# AGENTS.md instructions\nSkip"}],
                            },
                            {"role": "user", "content": [{"type": "input_text", "text": "What is this project?"}]},
                        ],
                    },
                },
                "response": {"status": 200, "body": {"model": "gpt-5.5", "usage": {"input_tokens": 1}}},
            }
        ],
    )

    _seed_legacy(tmp_path)
    summary = list_trace_sessions()[0]

    assert summary["first_user"] == "What is this project?"


def test_dashboard_loads_session_by_id(trace_db, tmp_path: Path) -> None:
    trace_path = tmp_path / "2026-05-20" / "trace_080000.jsonl"
    _write_jsonl(trace_path, [_anthropic_record()])
    _seed_legacy(tmp_path)
    session_id = list_trace_sessions()[0]["id"]

    payload = load_trace_session(session_id)

    assert payload is not None
    assert payload["session"]["legacy_rel_path"] == "2026-05-20/trace_080000.jsonl"
    assert payload["records"][0]["request_id"] == "req_claude"


def test_dashboard_rejects_missing_session_ids(trace_db) -> None:
    template = read_dashboard_template()
    assert "session-list" in template
    assert "lang-select" in template
    assert "DASHBOARD_I18N" in template
    assert 'data-i18n="table_first_message"' in template
    assert "export_jsonl" in template
    assert load_trace_session("not-a-valid-session-id") is None


def test_dashboard_detail_navigation_uses_standalone_viewer_route() -> None:
    template = read_dashboard_template()

    assert "function sessionDetailUrl(sessionId)" in template
    assert "window.location.assign(sessionDetailUrl(sessionId))" in template
    assert "/dashboard/session/" in template
    assert "detailRecordTotal: 0" in template
    assert 'detailFingerprint: ""' in template
    assert "detailSession: null" in template
    assert "function detailRecordFetchLimit(sessionId, preserveLoaded)" in template
    assert "function sessionDetailFingerprint(session)" in template
    assert "function updateDetailSessionSummary(session)" in template
    assert "function updateDetailI18n(session)" in template
    assert "if (!selected || !state.detailSessionId || refreshDetail)" in template
    assert "updateDetailSessionSummary(selected)" in template
    assert "updateDetailI18n(state.detailSession)" in template
    assert "knownTotal > previousTotal && previousTotal <= loadedRecords" in template
    assert "const limit = detailRecordFetchLimit(sessionId, preserveLoaded)" in template
    assert "state.detailRecordTotal = totalRecords" in template
    assert "state.detailFingerprint = sessionDetailFingerprint(session)" in template


def test_dashboard_summarize_session_and_migration(trace_db, tmp_path: Path) -> None:
    assert dashboard_trace_snapshot() == {}

    trace_path = tmp_path / "2026-05-20" / "trace_080000.jsonl"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text(
        '\nnot-json\n[]\n{"request_id":"ok","request":{},"response":{}}\n',
        encoding="utf-8",
    )
    manifest_path = tmp_path / ".cloudtap-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "traces": [
                    {
                        "client": "kimi",
                        "files": ["2026-05-20/trace_080000.jsonl"],
                        "created_at": "2026-05-20T00:00:00+00:00",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    _seed_legacy(tmp_path)
    summary = list_trace_sessions()[0]
    assert summary["agent"] == "Kimi"
    assert summary["record_count"] == 1


def test_dashboard_parses_provider_fallbacks(trace_db, tmp_path: Path) -> None:
    html_trace = tmp_path / "2026-05-20" / "trace_090000.jsonl"
    html_trace.parent.mkdir(parents=True, exist_ok=True)
    _write_jsonl(html_trace, [_antigravity_record()])
    _seed_legacy(tmp_path)

    session_id = list_trace_sessions()[0]["id"]
    summary = list_trace_sessions(current_session_id=session_id)[0]
    assert summary["status"] == "active"
    assert summary["model"] == "unknown"

    provider_cases = [
        ({"metadata": {"client": "agy"}}, [], "Antigravity"),
        ({}, [{"capture": {"client": "cursor"}}], "Cursor"),
        ({}, [{"request": {"headers": {"host": "generativelanguage.googleapis.com"}}}], "Gemini"),
        ({}, [{"request": {"path": "/v1/responses"}}], "Codex"),
        ({}, [{"request": {"headers": {"Host": "api.moonshot.cn"}}}], "Kimi"),
        ({}, [{"request": {"headers": {"Host": "qoder.example"}}}], "Qoder"),
        ({}, [{"request": {"headers": {"Host": "opencode.example"}}}], "OpenCode"),
        ({}, [{"request": {"headers": {"Host": "hermes.example"}}}], "Hermes"),
        ({}, [{"upstream_base_url": "https://api.anthropic.com/v1"}], "Claude Code"),
        ({}, [], "Unknown"),
    ]
    for manifest_entry, records, expected in provider_cases:
        assert _infer_agent(records, manifest_entry) == expected

    assert _record_host({"request": {"headers": {"host": "lowercase.example"}}}) == "lowercase.example"
    assert _record_host({"upstream_base_url": "https://upstream.example/path"}) == "upstream.example"


def test_dashboard_extracts_usage_models_errors_and_text() -> None:
    assert _record_usage({"response": {"body": {"usageMetadata": {"promptTokenCount": 3}}}})["input_tokens"] == 3
    assert (
        _record_usage(
            {"response": {"ws_events": [{"data": '{"response":{"usage":{"input_tokens":4,"output_tokens":2}}}'}]}}
        )["output_tokens"]
        == 2
    )
    assert _record_usage({"response": {"body": {"input_tokens": 5}}})["input_tokens"] == 5

    assert _record_model({"request": {"body": {"modelId": "gemini-3.1"}}}) == "gemini-3.1"
    assert _record_model({"request": {"body": {"request": {"model": "sonnet-4-6"}}}}) == "sonnet-4-6"
    assert _record_model({"response": {"body": {"model": "gpt-oss"}}}) == "gpt-oss"
    assert _record_model({"request": {"path": "/v1beta/models/gemini-pro:generateContent"}}) == "gemini-pro"
    assert _record_model({}) == ""

    assert _first_error([{"response": {"error": "failed hard"}}]) == "failed hard"
    assert _first_error([{"response": {"body": {"error": "body failed"}}}]) == "body failed"
    assert _first_error([{"response": {"body": {"error": {"message": "nested failed"}}}}]) == "nested failed"
    assert _first_error([{"response": {"body": {}}}]) == ""

    assert _request_user_text("raw prompt") == "raw prompt"
    assert _request_user_text(None) == ""
    assert _request_user_text({"prompt": "fallback prompt"}) == "fallback prompt"
    assert _request_user_text({"messages": [{"role": "user", "content": "<session>\nwrapped prompt\n</session>"}]}) == (
        "wrapped prompt"
    )
    assert (
        _request_user_text(
            {
                "request": {
                    "contents": [
                        {"role": "user", "parts": [{"text": "<session_context>\ncontext\n</session_context>"}]},
                        {"role": "user", "parts": [{"text": "actual Gemini prompt"}]},
                    ]
                }
            }
        )
        == "actual Gemini prompt"
    )
    assert (
        _request_user_text(
            {
                "request": {
                    "contents": [
                        {
                            "role": "user",
                            "parts": [
                                {
                                    "text": "<USER_REQUEST>\n--print-timeout\n</USER_REQUEST>\n"
                                    "<ADDITIONAL_METADATA>time</ADDITIONAL_METADATA>"
                                }
                            ],
                        }
                    ]
                }
            }
        )
        == "--print-timeout"
    )
    assert _request_user_text({"input": [{"type": "message", "content": [{"text": "input text"}]}]}) == "input text"
    assert (
        _request_user_text(
            {
                "input": [
                    {"role": "developer", "content": [{"type": "input_text", "text": "developer setup"}]},
                    {"type": "function_call_output", "output": "tool result"},
                    {"role": "user", "content": [{"type": "input_text", "text": "raw user prompt"}]},
                ]
            }
        )
        == "raw user prompt"
    )
    assert _request_user_text({"messages": [{"role": "user", "content": ["hello", {"text": "world"}]}]}) == (
        "hello\nworld"
    )
    assert (
        _request_user_text(
            {"contents": [{"role": "model", "parts": [{"text": "skip"}]}, {"role": "USER", "parts": [{"text": "use"}]}]}
        )
        == "use"
    )

    assert _response_text("raw response") == "raw response"
    assert _response_text(None) == ""
    assert _response_text({"choices": [{"message": {"content": "choice"}}]}) == "choice"
    assert _response_text({"choices": [{"delta": {"content": [{"text": "delta"}]}}]}) == "delta"
    assert _response_text({"output": [{"output_text": "out"}]}) == "out"
    assert _response_text({"response": {"content": "response field"}}) == "response field"
    assert _content_text({"text": ["nested", {"content": "dict"}]}) == "nested\ndict"
    assert _content_text({"input_text": "typed prompt"}) == "typed prompt"
    assert _content_text([{"type": "message", "content": [{"output_text": "message text"}]}]) == "message text"
    assert _input_user_text([{"role": "developer", "content": "dev"}, {"content": "implicit user"}]) == "implicit user"
    assert _clean_user_prompt_text('"quoted prompt"') == "quoted prompt"
    assert _clean_user_prompt_text("<system-reminder>\nskip\n</system-reminder>") == ""
    assert _parts_text("not-list") == ""
    assert _preview(" a \n b ", 20) == "a b"
    assert _preview("abcdef", 4) == "abc..."

    assert _response_events({"response": "bad"}) == []
    assert _response_events({"response": {"sse_events": [{"data": "{}"}, "bad"]}}) == [{"data": "{}"}]
    assert _event_payload({"data": "not-json"}) == {}
    assert _event_payload({"data": {"response": {"content": "payload"}}}) == {"content": "payload"}
    assert _event_payload({"data": 1}) == {}

    assert _record_response_text({"response": {"body": "body text"}}) == "body text"
    assert (
        _record_response_text(
            {"response": {"ws_events": [{"item": {"content": "item text"}}, {"part": {"text": "part text"}}]}}
        )
        == "part text"
    )
    assert _record_response_text({"response": {"ws_events": [{"text": "event text"}]}}) == "event text"
    assert (
        _record_response_text({"response": {"ws_events": [{"data": '{"content":"payload text"}'}]}}) == "payload text"
    )
    assert _record_response_text({"response": {}}) == ""


def test_dashboard_preview_skips_auxiliary_auth_records(trace_db, tmp_path: Path) -> None:
    trace_path = tmp_path / "2026-05-20" / "trace_100000.jsonl"
    _write_jsonl(
        trace_path,
        [
            {
                "timestamp": "2026-05-20T10:00:00+00:00",
                "turn": 1,
                "request": {
                    "method": "POST",
                    "path": "/token",
                    "body": "refresh_token=secret-token&client_id=client",
                },
                "response": {"status": 200, "body": {}},
            },
            {
                "timestamp": "2026-05-20T10:00:01+00:00",
                "turn": 2,
                "request": {"method": "POST", "path": "/log?format=json", "body": {}},
                "response": {"status": 403, "body": "<!DOCTYPE html> challenge page"},
            },
            {
                "timestamp": "2026-05-20T10:00:02+00:00",
                "turn": 3,
                "request": {
                    "method": "POST",
                    "path": "/v1internal:streamGenerateContent?alt=sse",
                    "headers": {"Host": "generativelanguage.googleapis.com"},
                    "body": {
                        "request": {
                            "contents": [
                                {
                                    "role": "user",
                                    "parts": [{"text": "Gemini dashboard prompt"}],
                                }
                            ]
                        }
                    },
                },
                "response": {
                    "status": 200,
                    "body": {
                        "candidates": [
                            {
                                "content": {
                                    "parts": [{"text": "Gemini dashboard response."}],
                                }
                            }
                        ]
                    },
                },
            },
        ],
    )

    _seed_legacy(tmp_path)
    summary = list_trace_sessions()[0]

    assert summary["first_user"] == "Gemini dashboard prompt"
    assert summary["last_response"] == "Gemini dashboard response."


@pytest.mark.asyncio
async def test_dashboard_server_serves_session_api_and_exports(trace_db, tmp_path: Path) -> None:
    trace_path = tmp_path / "2026-05-20" / "trace_080000.jsonl"
    second_trace_path = tmp_path / "2026-05-20" / "trace_081500.jsonl"
    _write_jsonl(trace_path, [_anthropic_record()])
    _write_jsonl(second_trace_path, [_anthropic_record(turn=2), _anthropic_record(turn=3)])
    trace_path.with_suffix(".log").write_text("10:00:00 proxy log\n", encoding="utf-8")
    _seed_legacy(tmp_path)

    server = LiveViewerServer(port=0, migrate_from=tmp_path, dashboard_mode=True)
    port = await server.start()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/") as resp:
                assert resp.status == 200
                html = await resp.text()
                assert "session-list" in html
                assert "export_jsonl" in html

            async with session.get(f"http://127.0.0.1:{port}/api/sessions") as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert len(payload["sessions"]) == 2
                second_session_id = next(item["id"] for item in payload["sessions"] if item["record_count"] == 2)
                session_id = next(item["id"] for item in payload["sessions"] if item["record_count"] == 1)

            async with session.get(
                f"http://127.0.0.1:{port}/dashboard?session_id={session_id}",
                allow_redirects=False,
            ) as resp:
                assert resp.status == 302
                assert resp.headers["Location"] == f"/dashboard/session/{session_id}"

            async with session.get(f"http://127.0.0.1:{port}/dashboard/session/{session_id}") as resp:
                assert resp.status == 200
                html = await resp.text()
                assert "EMBEDDED_TRACE_DATA" in html
                assert "req_claude" in html
                assert f'const __TRACE_JSONL_PATH__ = "/api/sessions/{session_id}/export/jsonl";' in html
                assert f'const __TRACE_HTML_PATH__ = "/dashboard/session/{session_id}";' in html
                assert f"session-{session_id[:8]}.jsonl" not in html
                assert f"session-{session_id[:8]}.html" not in html
                assert "__TRACE_SESSION_EXPORTS__" in html
                assert f"/api/sessions/{session_id}/export/jsonl" in html
                assert f"/api/sessions/{session_id}/export/log" in html
                assert "session-list" not in html
                assert "back-to-list" not in html

            async with session.get(f"http://127.0.0.1:{port}/api/agents") as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert payload["agents"][0]["label"] == "Claude Code"

            async with session.get(f"http://127.0.0.1:{port}/api/sessions/{session_id}/records") as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert payload["records"][0]["request_id"] == "req_claude"

            async with session.get(
                f"http://127.0.0.1:{port}/api/sessions/{session_id}/html",
                allow_redirects=False,
            ) as resp:
                assert resp.status == 200
                html = await resp.text()
                assert "EMBEDDED_TRACE_DATA" in html
                assert "req_claude" in html
                assert f'const __TRACE_JSONL_PATH__ = "/api/sessions/{session_id}/export/jsonl";' in html
                assert f'const __TRACE_HTML_PATH__ = "/dashboard/session/{session_id}";' in html
                assert f"session-{session_id[:8]}.jsonl" not in html
                assert f"session-{session_id[:8]}.html" not in html

            async with session.get(
                f"http://127.0.0.1:{port}/api/sessions/{second_session_id}/records?offset=1&limit=1"
            ) as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert payload["session"]["record_count"] == 2
                assert [record["turn"] for record in payload["records"]] == [3]

            async with session.get(f"http://127.0.0.1:{port}/api/sessions/{session_id}/export/jsonl") as resp:
                assert resp.status == 200
                body = await resp.text()
                assert "req_claude" in body

            async with session.get(f"http://127.0.0.1:{port}/api/sessions/{session_id}/export/log") as resp:
                assert resp.status == 200
                assert resp.content_type == "text/plain"
                assert resp.charset == "utf-8"
                body = await resp.text()
                assert body == "10:00:00 proxy log\n"

            async with session.get(f"http://127.0.0.1:{port}/api/sessions/bad/records") as resp:
                assert resp.status == 404
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_dashboard_server_sse_events(trace_db) -> None:
    server = LiveViewerServer(port=0, dashboard_mode=True)
    port = await server.start()
    try:
        timeout = aiohttp.ClientTimeout(total=3)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"http://127.0.0.1:{port}/api/agents") as resp:
                assert resp.status == 200
                assert await resp.json() == {"agents": []}

            async with session.get(f"http://127.0.0.1:{port}/api/sessions") as resp:
                assert resp.status == 200
                assert await resp.json() == {"sessions": []}

            async with session.get(f"http://127.0.0.1:{port}/api/sessions/anything/records") as resp:
                assert resp.status == 404

            async with session.get(f"http://127.0.0.1:{port}/dashboard/events") as resp:
                assert resp.status == 200
                assert await asyncio.wait_for(resp.content.readline(), timeout=1) == b"event: ready\n"
                ready_data = await asyncio.wait_for(resp.content.readline(), timeout=1)
                assert b'"type":"ready"' in ready_data
                assert await asyncio.wait_for(resp.content.readline(), timeout=1) == b"\n"

                await server._broadcast_dashboard_event({"type": "refresh"})
                assert await asyncio.wait_for(resp.content.readline(), timeout=1) == b"event: refresh\n"
                refresh_data = await asyncio.wait_for(resp.content.readline(), timeout=1)
                assert b'"type":"refresh"' in refresh_data
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_dashboard_session_route_serves_standalone_viewer(trace_db, tmp_path: Path) -> None:
    playwright = pytest.importorskip("playwright.async_api")
    trace_path = tmp_path / "2026-05-20" / "trace_080000.jsonl"
    _write_jsonl(trace_path, [_anthropic_record(turn=turn) for turn in range(1, 13)])
    _seed_legacy(tmp_path)
    session_id = list_trace_sessions()[0]["id"]

    server = LiveViewerServer(port=0, migrate_from=tmp_path, dashboard_mode=True)
    port = await server.start()
    try:
        async with playwright.async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(
                    f"http://127.0.0.1:{port}/dashboard/session/{session_id}",
                    wait_until="domcontentloaded",
                )
                await page.wait_for_selector(".sidebar-item", timeout=5000)
                assert await page.locator(".header").count() == 1
                assert await page.locator(".viewer-frame").count() == 0
                assert await page.locator("#back-to-list").count() == 0
                assert await page.locator("#session-list").count() == 0
                assert await page.locator(".sidebar-item").count() >= 10
                export_links = page.locator("#viewer-actions .viewer-action")
                assert await export_links.count() == 2
                hrefs = await export_links.evaluate_all("(links) => links.map((link) => link.getAttribute('href'))")
                assert f"/api/sessions/{session_id}/export/jsonl" in hrefs
                assert f"/api/sessions/{session_id}/export/log" in hrefs
            finally:
                await browser.close()
    finally:
        await server.stop()


def test_live_viewer_exposes_current_session_id(trace_db) -> None:
    session_id = get_trace_store().create_session(client="claude", proxy_mode="reverse")
    server = LiveViewerServer(session_id=session_id)
    assert server.session_id == session_id

    assert LiveViewerServer().session_id is None


def test_sqlite_log_handler_exports_single_timestamp(trace_db) -> None:
    store = get_trace_store()
    session_id = store.create_session(client="claude", proxy_mode="reverse")
    handler = SQLiteLogHandler(session_id, store=store)
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "proxy started", (), None)
    record.created = datetime(2026, 5, 20, 8, 0, tzinfo=timezone.utc).timestamp()

    handler.emit(record)

    assert store.export_log(session_id) == "08:00:00 proxy started\n"


@pytest.mark.asyncio
async def test_trace_writer_adds_capture_metadata(trace_db) -> None:
    store = get_trace_store()
    session_id = store.create_session(client="codex", proxy_mode="forward")
    writer = TraceWriter(session_id, store=store, metadata={"client": "codex", "proxy_mode": "forward"})
    try:
        await writer.write(_anthropic_record())
    finally:
        writer.close()

    record = store.load_records(session_id)[0]
    assert record["capture"] == {"client": "claude", "proxy_mode": "reverse"}

    session_id = store.create_session(client="codex", proxy_mode="forward")
    writer = TraceWriter(session_id, store=store, metadata={"client": "codex", "proxy_mode": "forward"})
    try:
        await writer.write({"request": {"body": {}}, "response": {"body": {}}})
    finally:
        writer.close()

    records = store.load_records(session_id)
    assert records[-1]["capture"] == {"client": "codex", "proxy_mode": "forward"}


@pytest.mark.asyncio
async def test_trace_writer_persists_records_to_sqlite(trace_db) -> None:
    store = get_trace_store()
    session_id = store.create_session(client="claude", proxy_mode="reverse")
    writer = TraceWriter(session_id, store=store, metadata={"client": "claude", "proxy_mode": "reverse"})
    try:
        await writer.write(_anthropic_record())
    finally:
        writer.close()

    records = store.load_records(session_id)

    assert len(records) == 1
    assert records[0]["capture"]["client"] == "claude"
