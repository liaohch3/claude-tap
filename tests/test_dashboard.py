import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import pytest
from aiohttp.test_utils import make_mocked_request

from claude_tap.dashboard import (
    DASHBOARD_SUMMARY_VERSION,
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


def _seed_dashboard_summary(
    *,
    session_id: str,
    agent: str,
    status: str,
    record_count: int,
    first_user: str,
    updated_at: str,
    date_key: str,
) -> str:
    return json.dumps(
        {
            "id": session_id,
            "summary_version": DASHBOARD_SUMMARY_VERSION,
            "date": date_key,
            "agent": agent,
            "status": status,
            "record_count": record_count,
            "turn_count": record_count,
            "updated_at": updated_at,
            "first_user": first_user,
            "last_response": "",
            "model": "gpt-5.5",
            "total_tokens": 0,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


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


def test_dashboard_summarizes_null_usage_token_fields(trace_db, tmp_path: Path) -> None:
    trace_path = tmp_path / "2026-05-20" / "trace_082000.jsonl"
    record = _anthropic_record()
    record["response"]["body"]["usage"] = {
        "input_tokens": 42,
        "output_tokens": 9,
        "cache_read_input_tokens": None,
        "cache_creation_input_tokens": None,
    }
    _write_jsonl(trace_path, [record])
    _seed_legacy(tmp_path)

    summary = list_trace_sessions()[0]

    assert summary["input_tokens"] == 42
    assert summary["output_tokens"] == 9
    assert summary["cache_read_tokens"] == 0
    assert summary["cache_create_tokens"] == 0
    assert summary["total_tokens"] == 51


def test_dashboard_load_session_can_page_sqlite_records(trace_db, tmp_path: Path) -> None:
    trace_path = tmp_path / "2026-05-20" / "trace_080000.jsonl"
    _write_jsonl(trace_path, [_anthropic_record(), _anthropic_record(turn=2), _anthropic_record(turn=3)])
    _seed_legacy(tmp_path)
    session_id = list_trace_sessions()[0]["id"]

    payload = load_trace_session(session_id, record_limit=1, record_offset=1)

    assert payload is not None
    assert payload["session"]["record_count"] == 3
    assert [record["turn"] for record in payload["records"]] == [2]


def test_dashboard_lists_stale_cached_summary_without_record_scan(trace_db, monkeypatch) -> None:
    store = get_trace_store()
    session_id = store.create_session(client="claude", proxy_mode="reverse")
    conn = store._connect()
    conn.execute(
        """
        UPDATE sessions
        SET status = 'active',
            record_count = 25,
            summary_json = ?
        WHERE id = ?
        """,
        (
            json.dumps(
                {
                    "agent": "Claude Code",
                    "agent_key": "claude-code",
                    "record_count": 25,
                    "total_tokens": 500,
                    "first_user": "Cached prompt",
                },
                separators=(",", ":"),
            ),
            session_id,
        ),
    )
    conn.commit()

    def fail_load_records(*_args, **_kwargs):
        raise AssertionError("list view must not load full records")

    monkeypatch.setattr(store, "load_records", fail_load_records)

    summary = list_trace_sessions()[0]

    assert summary["id"] == session_id
    assert summary["record_count"] == 25
    assert summary["status"] == "active"
    assert summary["first_user"] == "Cached prompt"


def test_dashboard_lists_uncached_session_without_record_scan(trace_db, monkeypatch) -> None:
    store = get_trace_store()
    session_id = store.create_session(client="codex", proxy_mode="reverse")
    conn = store._connect()
    conn.execute(
        "UPDATE sessions SET status = 'complete', record_count = 7, summary_json = NULL WHERE id = ?",
        (session_id,),
    )
    conn.commit()

    def fail_load_records(*_args, **_kwargs):
        raise AssertionError("list view must not load full records")

    monkeypatch.setattr(store, "load_records", fail_load_records)

    summary = list_trace_sessions()[0]

    assert summary["id"] == session_id
    assert summary["record_count"] == 7
    assert summary["agent"] == "Codex"
    assert summary["status"] == "complete"


def test_dashboard_agent_buckets_use_aggregate_query(trace_db, monkeypatch) -> None:
    store = get_trace_store()
    codex_id = store.create_session(client="codex", proxy_mode="reverse")
    claude_id = store.create_session(client="claude", proxy_mode="reverse")
    conn = store._connect()
    conn.execute(
        """
        UPDATE sessions
        SET status = 'complete', record_count = 3, summary_json = ?
        WHERE id = ?
        """,
        (
            _seed_dashboard_summary(
                session_id=codex_id,
                agent="Codex",
                status="complete",
                record_count=3,
                first_user="Codex prompt",
                updated_at="2026-06-01T10:00:00+00:00",
                date_key="2026-06-01",
            ),
            codex_id,
        ),
    )
    conn.execute(
        """
        UPDATE sessions
        SET status = 'complete', record_count = 2, summary_json = ?
        WHERE id = ?
        """,
        (
            _seed_dashboard_summary(
                session_id=claude_id,
                agent="Claude Code",
                status="complete",
                record_count=2,
                first_user="Claude prompt",
                updated_at="2026-06-01T11:00:00+00:00",
                date_key="2026-06-01",
            ),
            claude_id,
        ),
    )
    conn.commit()

    def fail_list_session_rows(*_args, **_kwargs):
        raise AssertionError("agent buckets must not load every session row")

    monkeypatch.setattr(store, "list_session_rows", fail_list_session_rows)

    agents = list_trace_agents()

    assert [(agent["label"], agent["sessions"], agent["records"]) for agent in agents] == [
        ("Claude Code", 1, 2),
        ("Codex", 1, 3),
    ]


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


def test_dashboard_first_message_skips_injected_user_content_blocks(trace_db, tmp_path: Path) -> None:
    trace_path = tmp_path / "2026-05-20" / "trace_101500.jsonl"
    _write_jsonl(
        trace_path,
        [
            {
                "timestamp": "2026-05-20T10:15:00+00:00",
                "turn": 1,
                "request": {
                    "method": "POST",
                    "path": "/v1/messages",
                    "body": {
                        "model": "claude-sonnet-4-6",
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": "<system-reminder>\nInjected context\n</system-reminder>",
                                    },
                                    {"type": "text", "text": "Fix the failing dashboard prompt preview."},
                                ],
                            }
                        ],
                    },
                },
                "response": {"status": 200, "body": {"model": "claude-sonnet-4-6", "usage": {"input_tokens": 1}}},
            }
        ],
    )

    _seed_legacy(tmp_path)
    summary = list_trace_sessions()[0]

    assert summary["first_user"] == "Fix the failing dashboard prompt preview."


def test_dashboard_recomputes_stale_first_message_summary_cache(trace_db) -> None:
    store = get_trace_store()
    session_id = store.create_session(
        client="claude",
        proxy_mode="reverse",
        started_at=datetime(2026, 5, 20, 10, 15, tzinfo=timezone.utc),
    )
    store.append_record(
        session_id,
        {
            "timestamp": "2026-05-20T10:15:00+00:00",
            "turn": 1,
            "request": {
                "method": "POST",
                "path": "/v1/messages",
                "body": {
                    "model": "claude-sonnet-4-6",
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "<system-reminder>\nInjected context\n</system-reminder>"},
                                {"type": "tool_result", "content": "stale tool output"},
                                {"type": "text", "text": "Fix the failing dashboard prompt preview."},
                            ],
                        }
                    ],
                },
            },
            "response": {"status": 200, "body": {"model": "claude-sonnet-4-6", "usage": {"input_tokens": 1}}},
        },
    )

    stale_summary = {
        "id": session_id,
        "status": "complete",
        "record_count": 1,
        "updated_at": "2026-05-20T10:15:00+00:00",
        "first_user": "stale tool output",
    }
    conn = store._connect()
    conn.execute(
        "UPDATE sessions SET status = 'complete', summary_json = ? WHERE id = ?",
        (json.dumps(stale_summary, ensure_ascii=False, separators=(",", ":")), session_id),
    )
    conn.commit()

    summary = list_trace_sessions()[0]
    cached = json.loads(store.load_session_row(session_id)["summary_json"])

    assert summary["first_user"] == "Fix the failing dashboard prompt preview."
    assert cached["first_user"] == "Fix the failing dashboard prompt preview."
    assert cached["summary_version"] == DASHBOARD_SUMMARY_VERSION


def test_dashboard_recomputes_stale_active_summary_cache_on_append(trace_db) -> None:
    store = get_trace_store()
    session_id = store.create_session(
        client="claude",
        proxy_mode="reverse",
        started_at=datetime(2026, 5, 20, 10, 15, tzinfo=timezone.utc),
    )
    first_record = {
        "timestamp": "2026-05-20T10:15:00+00:00",
        "turn": 1,
        "request": {
            "method": "POST",
            "path": "/v1/messages",
            "body": {
                "model": "claude-sonnet-4-6",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "<system-reminder>\nInjected context\n</system-reminder>"},
                            {"type": "tool_result", "content": "stale tool output"},
                            {"type": "text", "text": "Fix the active dashboard prompt preview."},
                        ],
                    }
                ],
            },
        },
        "response": {"status": 200, "body": {"model": "claude-sonnet-4-6", "usage": {"input_tokens": 1}}},
    }
    store.append_record(session_id, first_record)

    stale_summary = {
        "id": session_id,
        "status": "active",
        "record_count": 1,
        "updated_at": "2026-05-20T10:15:00+00:00",
        "first_user": "stale tool output",
    }
    conn = store._connect()
    conn.execute(
        "UPDATE sessions SET status = 'active', summary_json = ? WHERE id = ?",
        (json.dumps(stale_summary, ensure_ascii=False, separators=(",", ":")), session_id),
    )
    conn.commit()
    store.append_record(
        session_id,
        {
            "timestamp": "2026-05-20T10:16:00+00:00",
            "turn": 2,
            "request": {"method": "POST", "path": "/v1/messages", "body": {"model": "claude-sonnet-4-6"}},
            "response": {"status": 200, "body": {"model": "claude-sonnet-4-6", "usage": {"input_tokens": 1}}},
        },
    )

    summary = list_trace_sessions(current_session_id=session_id)[0]
    cached = json.loads(store.load_session_row(session_id)["summary_json"])

    assert summary["first_user"] == "Fix the active dashboard prompt preview."
    assert cached["first_user"] == "Fix the active dashboard prompt preview."
    assert cached["summary_version"] == DASHBOARD_SUMMARY_VERSION
    assert cached["record_count"] == 2


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
    assert "export_html" in template
    assert "export_menu" in template
    assert load_trace_session("not-a-valid-session-id") is None


def test_dashboard_detail_navigation_uses_lazy_shell_route() -> None:
    template = read_dashboard_template()

    assert "function sessionDetailUrl(sessionId)" in template
    assert "window.location.assign(sessionDetailUrl(sessionId))" in template
    assert "/dashboard/session/" in template
    assert "detailRecordTotal: 0" in template
    assert 'detailFingerprint: ""' in template
    assert "detailSession: null" in template
    assert 'activeTab: "raw"' in template
    assert "function detailRecordFetchLimit(sessionId, preserveLoaded)" in template
    assert "function sessionDetailFingerprint(session)" in template
    assert "function updateDetailSessionSummary(session)" in template
    assert "function updateDetailI18n(session)" in template
    assert 'params.set("search", search)' in template
    assert 'params.set("agent", state.selectedAgent)' in template
    assert "function refreshForFilters()" in template
    assert 'state.view === "detail" && state.selectedSessionId' in template
    assert "const detailLoaded = state.detailSessionId === state.selectedSessionId" in template
    assert "updateDetailSessionSummary(selected)" in template
    assert "updateDetailI18n(state.detailSession)" in template
    assert "knownTotal > previousTotal && previousTotal <= loadedRecords" in template
    assert "const limit = detailRecordFetchLimit(sessionId, preserveLoaded)" in template
    assert "state.detailRecordTotal = totalRecords" in template
    assert "state.detailFingerprint = sessionDetailFingerprint(session)" in template
    assert 'state.activeTab === "conversation" ? `' in template


def test_dashboard_template_exposes_session_delete_controls() -> None:
    template = read_dashboard_template()

    assert 'data-i18n="table_actions"' in template
    assert 'id="edit-sessions"' in template
    assert 'id="select-all-sessions"' in template
    assert 'id="delete-selected-sessions"' in template
    assert "data-select-session" in template
    assert "delete-session-modal" in template
    assert "data-delete-session" not in template
    assert "delete_active_session_title" in template
    assert "session.active" in template
    assert "function isSessionRowActionTarget(target)" in template
    assert "event.target !== row" in template
    assert "function confirmDeleteSession()" in template
    assert "body: JSON.stringify({session_ids: sessionIds})" in template
    assert 'method: "DELETE"' in template


def test_dashboard_template_exposes_quit_control() -> None:
    template = read_dashboard_template()

    assert 'id="dashboard-quit"' in template
    assert "quit_dashboard_confirm" in template
    assert "Stop dashboard service" in template
    assert "function quitDashboard()" in template
    assert 'const DASHBOARD_QUIT_TOKEN = "";' in template
    assert "const DASHBOARD_CAN_STOP = false;" in template
    assert '"X-Claude-Tap-Dashboard-Token": DASHBOARD_QUIT_TOKEN' in template
    assert "let dashboardEvents = null;" in template
    assert "function closeDashboardEvents()" in template
    assert "if (state.quittingDashboard) return;" in template


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
    assert (
        _request_user_text(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "<USER_REQUEST>\n--print-timeout\n</USER_REQUEST>"},
                            {"type": "text", "text": "<ADDITIONAL_METADATA>time</ADDITIONAL_METADATA>"},
                        ],
                    }
                ]
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
    assert (
        _request_user_text(
            {
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "<system-reminder>\nskip\n</system-reminder>"},
                            {"type": "text", "text": "actual response prompt"},
                        ],
                    }
                ]
            }
        )
        == "actual response prompt"
    )
    assert _request_user_text({"messages": [{"role": "user", "content": ["hello", {"text": "world"}]}]}) == (
        "hello\nworld"
    )
    assert (
        _request_user_text(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "<system-reminder>\nskip\n</system-reminder>"},
                            {"type": "text", "text": "actual message prompt"},
                        ],
                    }
                ]
            }
        )
        == "actual message prompt"
    )
    assert (
        _request_user_text(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "<system-reminder>\nskip\n</system-reminder>"},
                            {"type": "tool_result", "content": "tool output should not be first prompt"},
                            {"type": "function_call_output", "output": "function output should not be first prompt"},
                            {"type": "text", "text": "actual prompt after tools"},
                        ],
                    }
                ]
            }
        )
        == "actual prompt after tools"
    )
    assert (
        _request_user_text(
            {"contents": [{"role": "model", "parts": [{"text": "skip"}]}, {"role": "USER", "parts": [{"text": "use"}]}]}
        )
        == "use"
    )
    assert (
        _request_user_text(
            {
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {"text": "<session_context>\nskip\n</session_context>"},
                            {"text": "actual gemini prompt"},
                        ],
                    }
                ]
            }
        )
        == "actual gemini prompt"
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
                assert "export_html" in html
                assert "export_menu" in html

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
                assert "session-list" in html
                assert "back-to-list" in html
                assert "EMBEDDED_TRACE_COMPACT_DATA" not in html
                assert "req_claude" not in html
                assert "/api/sessions/${encodeURIComponent(session.id)}/html" in html

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
                assert "EMBEDDED_TRACE_COMPACT_DATA" in html
                assert "req_claude" in html
                assert f'const __TRACE_JSONL_PATH__ = "/api/sessions/{session_id}/export/compact";' in html
                assert f'const __TRACE_HTML_PATH__ = "/dashboard/session/{session_id}";' in html
                assert f"session-{session_id[:8]}.jsonl" not in html
                assert f"session-{session_id[:8]}.html" not in html
                assert f"/api/sessions/{session_id}/export/html" in html

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

            async with session.get(f"http://127.0.0.1:{port}/api/sessions/{session_id}/export/compact") as resp:
                assert resp.status == 200
                assert resp.content_type == "application/json"
                body = await resp.text()
                assert "__claude_tap_compact_trace__" in body
                assert "req_claude" in body

            async with session.get(f"http://127.0.0.1:{port}/api/sessions/{session_id}/export/log") as resp:
                assert resp.status == 200
                assert resp.content_type == "text/plain"
                assert resp.charset == "utf-8"
                body = await resp.text()
                assert body == "10:00:00 proxy log\n"

            async with session.get(f"http://127.0.0.1:{port}/api/sessions/{session_id}/export/html") as resp:
                assert resp.status == 200
                assert resp.content_type == "text/html"
                assert resp.charset == "utf-8"
                assert f'filename="trace_{session_id[:8]}.html"' in resp.headers["Content-Disposition"]
                html = await resp.text()
                assert "EMBEDDED_TRACE_DATA" in html
                assert "req_claude" in html
                assert f'const __TRACE_JSONL_PATH__ = "/api/sessions/{session_id}/export/jsonl";' in html
                assert f'const __TRACE_HTML_PATH__ = "/api/sessions/{session_id}/export/html";' in html
                assert f"session-{session_id[:8]}.jsonl" not in html

            async with session.delete(f"http://127.0.0.1:{port}/api/sessions/{second_session_id}") as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert payload["deleted_sessions"] == 1
                assert payload["deleted_records"] == 2

            async with session.get(f"http://127.0.0.1:{port}/api/sessions/{second_session_id}/records") as resp:
                assert resp.status == 404

            async with session.get(f"http://127.0.0.1:{port}/api/sessions") as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert [item["id"] for item in payload["sessions"]] == [session_id]

            async with session.get(f"http://127.0.0.1:{port}/api/sessions/bad/records") as resp:
                assert resp.status == 404
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_dashboard_session_filters_apply_before_paging(trace_db) -> None:
    store = get_trace_store()
    conn = store._connect()
    for index in range(120):
        session_id = store.create_session(
            client="claude",
            proxy_mode="reverse",
            started_at=datetime(2026, 6, 1, 12, index % 60, tzinfo=timezone.utc),
        )
        updated_at = f"2026-06-01T12:{index % 60:02d}:{index // 60:02d}+00:00"
        conn.execute(
            """
            UPDATE sessions
            SET status = 'complete',
                record_count = 1,
                updated_at = ?,
                date_key = '2026-06-01',
                summary_json = ?
            WHERE id = ?
            """,
            (
                updated_at,
                _seed_dashboard_summary(
                    session_id=session_id,
                    agent="Claude Code",
                    status="complete",
                    record_count=1,
                    first_user=f"Noise prompt {index}",
                    updated_at=updated_at,
                    date_key="2026-06-01",
                ),
                session_id,
            ),
        )
    target_id = store.create_session(
        client="codex",
        proxy_mode="reverse",
        started_at=datetime(2026, 5, 1, 8, 0, tzinfo=timezone.utc),
    )
    conn.execute(
        """
        UPDATE sessions
        SET status = 'error',
            record_count = 7,
            started_at = '2026-05-01T08:00:00+00:00',
            updated_at = '2026-05-01T08:00:00+00:00',
            date_key = '2026-05-01',
            summary_json = ?
        WHERE id = ?
        """,
        (
            _seed_dashboard_summary(
                session_id=target_id,
                agent="Codex",
                status="error",
                record_count=7,
                first_user="Find me past the first page",
                updated_at="2026-05-01T08:00:00+00:00",
                date_key="2026-05-01",
            ),
            target_id,
        ),
    )
    conn.commit()

    server = LiveViewerServer(port=0, dashboard_mode=True)
    port = await server.start()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/api/sessions?limit=100") as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert payload["total"] == 121
                assert payload["total_records"] == 127
                assert target_id not in {item["id"] for item in payload["sessions"]}

            for query in (
                "search=Find%20me%20past",
                "date=2026-05-01",
                "status=error",
                "agent=codex",
            ):
                async with session.get(f"http://127.0.0.1:{port}/api/sessions?limit=10&{query}") as resp:
                    assert resp.status == 200
                    payload = await resp.json()
                    assert payload["total"] == 1
                    assert payload["total_records"] == 7
                    assert [item["id"] for item in payload["sessions"]] == [target_id]
                    assert "2026-05-01" in payload["dates"]
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
                payload = await resp.json()
                assert payload["sessions"] == []
                assert payload["total"] == 0

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
async def test_dashboard_server_quit_route_stops_dashboard(trace_db) -> None:
    from claude_tap.shared_dashboard import is_dashboard_healthy, wait_for_dashboard_stopped

    server = LiveViewerServer(port=0, dashboard_mode=True)
    port = await server.start()
    try:
        timeout = aiohttp.ClientTimeout(total=3)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"http://127.0.0.1:{port}/dashboard") as resp:
                assert resp.status == 200
                html = await resp.text()
                assert f'const DASHBOARD_QUIT_TOKEN = "{server._dashboard_quit_token}";' in html
                assert "const DASHBOARD_CAN_STOP = true;" in html

            async with session.post(f"http://127.0.0.1:{port}/dashboard/quit") as resp:
                assert resp.status == 403
                payload = await resp.json()
                assert payload["ok"] is False

            async with session.get(f"http://127.0.0.1:{port}/dashboard/health") as resp:
                assert resp.status == 200
                health = await resp.json()
                assert health["quit_token"] == server._dashboard_quit_token

            async with session.post(
                f"http://127.0.0.1:{port}/dashboard/quit",
                headers={"X-Claude-Tap-Dashboard-Token": health["quit_token"]},
            ) as resp:
                assert resp.status == 200
                assert await resp.json() == {"ok": True}

        assert await wait_for_dashboard_stopped("127.0.0.1", port, timeout=2.0) is True
        assert await is_dashboard_healthy("127.0.0.1", port, require_current_db=False) is False
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_dashboard_quit_token_requires_trusted_host_and_origin(trace_db) -> None:
    from claude_tap.shared_dashboard import is_dashboard_healthy

    server = LiveViewerServer(port=0, dashboard_mode=True)
    port = await server.start()
    try:
        timeout = aiohttp.ClientTimeout(total=3)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                f"http://127.0.0.1:{port}/dashboard",
                headers={"Host": f"attacker.example:{port}", "Origin": f"http://attacker.example:{port}"},
            ) as resp:
                assert resp.status == 200
                html = await resp.text()
                assert 'const DASHBOARD_QUIT_TOKEN = "";' in html
                assert "const DASHBOARD_CAN_STOP = false;" in html
                assert "session-list" in html

            async with session.get(
                f"http://127.0.0.1:{port}/dashboard/health",
                headers={"Host": f"attacker.example:{port}", "Origin": f"http://attacker.example:{port}"},
            ) as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert payload["ok"] is True
                assert payload["dashboard_mode"] is True
                assert "quit_token" not in payload

            async with session.get(f"http://127.0.0.1:{port}/dashboard/health") as resp:
                assert resp.status == 200
                health = await resp.json()
                token = health["quit_token"]

            for headers in (
                {"Host": f"attacker.example:{port}", "X-Claude-Tap-Dashboard-Token": token},
                {
                    "Origin": f"http://attacker.example:{port}",
                    "X-Claude-Tap-Dashboard-Token": token,
                },
                {
                    "Origin": f"http://127.0.0.1:{port + 1}",
                    "X-Claude-Tap-Dashboard-Token": token,
                },
            ):
                async with session.post(f"http://127.0.0.1:{port}/dashboard/quit", headers=headers) as resp:
                    assert resp.status == 403
                    payload = await resp.json()
                    assert payload["ok"] is False

        assert await is_dashboard_healthy("127.0.0.1", port, require_current_db=False) is True
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_dashboard_quit_route_rejects_non_dashboard_server(trace_db) -> None:
    server = LiveViewerServer(port=0, dashboard_mode=False)
    port = await server.start()
    try:
        timeout = aiohttp.ClientTimeout(total=3)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(f"http://127.0.0.1:{port}/dashboard/quit") as resp:
                assert resp.status == 403
                payload = await resp.json()
                assert payload["ok"] is False
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
                page = await browser.new_page(accept_downloads=True)
                await page.goto(
                    f"http://127.0.0.1:{port}/dashboard/session/{session_id}",
                    wait_until="domcontentloaded",
                )
                await page.wait_for_selector("#raw-tab .section", timeout=5000)
                assert await page.locator(".header").count() == 1
                assert await page.locator(".viewer-frame").count() == 0
                assert await page.locator("#back-to-list").is_visible()
                assert await page.locator("#list-view.hidden").count() == 1
                assert await page.locator("#raw-tab .section").count() == 10
                assert await page.locator("[data-load-more]").count() == 1

                export_button = page.locator(".detail-inspector-bar .export-menu > summary")
                assert await export_button.count() == 1
                assert await export_button.inner_text() == "Export"
                assert await page.locator(".detail-inspector-bar .export-menu-item").count() == 4
                hrefs = await page.locator(".detail-inspector-bar .export-menu-item").evaluate_all(
                    "(links) => links.map((link) => link.getAttribute('href'))"
                )
                assert f"/api/sessions/{session_id}/export/jsonl" in hrefs
                assert f"/api/sessions/{session_id}/export/compact" in hrefs
                assert f"/api/sessions/{session_id}/export/log" in hrefs
                assert f"/api/sessions/{session_id}/export/html" in hrefs

                async with page.expect_download() as download_info:
                    await export_button.click()
                    await page.locator('.detail-inspector-bar .export-menu-item[href$="/export/html"]').click()
                download = await download_info.value
                assert download.suggested_filename == f"trace_{session_id[:8]}.html"
                download_path = await download.path()
                assert download_path is not None
                exported_html = Path(download_path).read_text(encoding="utf-8")
                assert "EMBEDDED_TRACE_DATA" in exported_html
                assert "req_claude" in exported_html
            finally:
                await browser.close()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_dashboard_session_export_menu_is_not_clipped_on_mobile(trace_db, tmp_path: Path) -> None:
    playwright = pytest.importorskip("playwright.async_api")
    trace_path = tmp_path / "2026-05-20" / "trace_080000.jsonl"
    _write_jsonl(trace_path, [_anthropic_record()])
    _seed_legacy(tmp_path)
    session_id = list_trace_sessions()[0]["id"]

    server = LiveViewerServer(port=0, migrate_from=tmp_path, dashboard_mode=True)
    port = await server.start()
    try:
        async with playwright.async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page = await browser.new_page(viewport={"width": 390, "height": 900})
                await page.goto(
                    f"http://127.0.0.1:{port}/dashboard/session/{session_id}",
                    wait_until="domcontentloaded",
                )
                await page.wait_for_selector(".detail-inspector-bar .export-menu > summary", timeout=5000)

                await page.locator(".detail-inspector-bar .export-menu > summary").click()
                menu = page.locator(".detail-inspector-bar .export-menu-list")
                assert await menu.is_visible()
                assert await page.locator(".detail-inspector-bar .export-menu-item").count() == 4

                layout = await page.evaluate(
                    """() => {
                      const actions = document.querySelector('.detail-inspector-bar .action-bar');
                      const menu = document.querySelector('.detail-inspector-bar .export-menu-list');
                      const actionsBox = actions.getBoundingClientRect();
                      const menuBox = menu.getBoundingClientRect();
                      const actionStyles = getComputedStyle(actions);
                      const menuStyles = getComputedStyle(menu);
                      return {
                        actionsBottom: actionsBox.bottom,
                        menuBottom: menuBox.bottom,
                        menuRight: menuBox.right,
                        viewportWidth: window.innerWidth,
                        menuHeight: menuBox.height,
                        overflowX: actionStyles.overflowX,
                        menuPosition: menuStyles.position,
                      };
                    }"""
                )
                assert layout["overflowX"] == "visible"
                assert layout["menuHeight"] > 80
                assert layout["menuRight"] <= layout["viewportWidth"]
                assert layout["menuBottom"] > layout["actionsBottom"]
            finally:
                await browser.close()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_dashboard_bulk_delete_edit_mode_focuses_confirmation_dialog(trace_db) -> None:
    playwright = pytest.importorskip("playwright.async_api")
    store = get_trace_store()
    session_id = store.create_session(client="claude", proxy_mode="reverse")
    store.append_record(session_id, _anthropic_record())
    store.finalize_session(session_id, {"api_calls": 1})

    server = LiveViewerServer(port=0, dashboard_mode=True)
    port = await server.start()
    try:
        async with playwright.async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(f"http://127.0.0.1:{port}/dashboard", wait_until="domcontentloaded")
                assert await page.locator("[data-delete-session]").count() == 0

                await page.locator("#edit-sessions").click()
                checkbox = page.locator(f'[data-select-session="{session_id}"]')
                await checkbox.wait_for(state="visible", timeout=5000)
                await checkbox.check()
                assert await page.locator("#bulk-selected-count").inner_text() == "1 selected"

                await page.locator("#delete-selected-sessions").click()
                await page.wait_for_selector("#delete-session-modal:not(.hidden)", timeout=5000)
                assert page.url == f"http://127.0.0.1:{port}/dashboard"
                assert await page.evaluate("document.activeElement && document.activeElement.id") == (
                    "delete-session-cancel"
                )

                await page.locator("#delete-session-confirm").click()
                await page.wait_for_selector("#delete-session-modal.hidden", state="attached", timeout=5000)
                await page.wait_for_selector(f'[data-session="{session_id}"]', state="detached", timeout=5000)
            finally:
                await browser.close()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_dashboard_delete_current_live_session_is_protected(trace_db) -> None:
    store = get_trace_store()
    session_id = store.create_session(client="claude", proxy_mode="reverse")

    server = LiveViewerServer(port=0, session_id=session_id, dashboard_mode=True)
    port = await server.start()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.delete(f"http://127.0.0.1:{port}/api/sessions/{session_id}") as resp:
                assert resp.status == 409
                payload = await resp.json()
                assert payload["error"] == "Live session cannot be deleted"
        assert store.load_session_row(session_id) is not None
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_dashboard_delete_active_session_is_protected(trace_db) -> None:
    store = get_trace_store()
    session_id = store.create_session(client="claude", proxy_mode="reverse")
    store.append_record(session_id, _anthropic_record())

    server = LiveViewerServer(port=0, dashboard_mode=True)
    port = await server.start()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/api/sessions") as resp:
                assert resp.status == 200
                payload = await resp.json()
                active_session = next(item for item in payload["sessions"] if item["id"] == session_id)
                assert active_session["active"] is True
                assert active_session["status"] == "active"

            async with session.delete(f"http://127.0.0.1:{port}/api/sessions/{session_id}") as resp:
                assert resp.status == 409
                payload = await resp.json()
                assert payload["error"] == "Active session cannot be deleted"
        assert store.load_session_row(session_id) is not None
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_dashboard_bulk_delete_skips_active_sessions(trace_db) -> None:
    store = get_trace_store()
    first_id = store.create_session(client="claude", proxy_mode="reverse")
    store.append_record(first_id, _anthropic_record())
    store.finalize_session(first_id, {"api_calls": 1})
    second_id = store.create_session(client="codex", proxy_mode="reverse")
    store.append_record(second_id, _anthropic_record(turn=2))
    store.finalize_session(second_id, {"api_calls": 1})
    active_id = store.create_session(client="claude", proxy_mode="reverse")
    store.append_record(active_id, _anthropic_record(turn=3))

    server = LiveViewerServer(port=0, dashboard_mode=True)
    port = await server.start()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.delete(
                f"http://127.0.0.1:{port}/api/sessions",
                json={"session_ids": [first_id, second_id, active_id, "missing"]},
            ) as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert payload["deleted_sessions"] == 2
                assert payload["deleted_records"] == 2
                assert payload["skipped_active_sessions"] == [active_id]
                assert payload["missing_sessions"] == ["missing"]

        assert store.load_session_row(first_id) is None
        assert store.load_session_row(second_id) is None
        assert store.load_session_row(active_id) is not None
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


def test_get_session_aggregates(trace_db) -> None:
    from claude_tap.trace_store import SessionQuery

    store = get_trace_store()
    conn = store._connect()

    # 1. Active session with no error
    active_id = store.create_session(client="claude", proxy_mode="reverse")
    conn.execute(
        "UPDATE sessions SET status = 'active', record_count = 5, summary_json = ? WHERE id = ?",
        (
            json.dumps({"agent": "Claude Code", "status": "active", "total_tokens": 120}, separators=(",", ":")),
            active_id,
        ),
    )

    # 2. Active session with error in summary_json
    active_err_id = store.create_session(client="claude", proxy_mode="reverse")
    conn.execute(
        "UPDATE sessions SET status = 'active', record_count = 2, summary_json = ? WHERE id = ?",
        (
            json.dumps({"agent": "Claude Code", "status": "error", "total_tokens": 80}, separators=(",", ":")),
            active_err_id,
        ),
    )

    # 3. Completed session with error status
    completed_err_id = store.create_session(client="claude", proxy_mode="reverse")
    conn.execute(
        "UPDATE sessions SET status = 'error', record_count = 10, summary_json = ? WHERE id = ?",
        (
            json.dumps({"agent": "Claude Code", "status": "error", "total_tokens": 300}, separators=(",", ":")),
            completed_err_id,
        ),
    )

    conn.commit()

    # Check global aggregates
    aggregates = store.get_session_aggregates()
    assert aggregates["total_sessions"] == 3
    assert aggregates["total_records"] == 17
    assert aggregates["total_tokens"] == 500
    assert aggregates["total_errors"] == 2  # active_err_id and completed_err_id

    # Check query-based status filtering for active status
    active_query = SessionQuery(status="active")
    active_aggs = store.get_session_aggregates(active_query)
    assert active_aggs["total_sessions"] == 1  # only active_id

    # Check query-based status filtering for error status
    error_query = SessionQuery(status="error")
    error_aggs = store.get_session_aggregates(error_query)
    assert error_aggs["total_sessions"] == 2  # active_err_id and completed_err_id


def test_agent_filter_values_resolves_custom_agents(trace_db) -> None:
    from claude_tap.dashboard import _agent_filter_values

    store = get_trace_store()
    conn = store._connect()

    # Create a session with a custom agent client "My-Agent"
    custom_id = store.create_session(client="My-Agent", proxy_mode="reverse")
    conn.execute(
        "UPDATE sessions SET status = 'complete', record_count = 1, summary_json = ? WHERE id = ?",
        (
            json.dumps({"agent": "My-Agent", "status": "complete", "total_tokens": 10}, separators=(",", ":")),
            custom_id,
        ),
    )
    conn.commit()

    clients, labels = _agent_filter_values("my-agent")
    assert "My-Agent" in clients
    assert "My-Agent" in labels


@pytest.mark.asyncio
async def test_search_uncached_records_fallback(trace_db) -> None:
    from claude_tap.trace_store import SessionQuery

    store = get_trace_store()
    conn = store._connect()

    # Create a session with NULL summary_json (uncached) but records with a specific text in payload_json
    session_id = store.create_session(client="claude", proxy_mode="reverse")
    writer = TraceWriter(session_id, store=store, metadata={"client": "claude", "proxy_mode": "reverse"})
    try:
        await writer.write(
            {
                "timestamp": "2026-05-20T10:15:00+00:00",
                "request": {"method": "POST", "path": "/v1/messages", "body": "unusual_secret_string"},
                "response": {"status": 200, "body": {}},
            }
        )
    finally:
        writer.close()

    # Force summary_json to be NULL to simulate uncached session
    conn.execute("UPDATE sessions SET summary_json = NULL WHERE id = ?", (session_id,))
    conn.commit()

    search_query = SessionQuery(search="unusual_secret_string")
    sessions = store.list_session_rows(query=search_query)
    assert len(sessions) == 1
    assert sessions[0]["id"] == session_id
