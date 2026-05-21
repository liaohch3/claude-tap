import asyncio
import json
from pathlib import Path

import aiohttp
import pytest

from claude_tap.dashboard import (
    _clean_user_prompt_text,
    _content_text,
    _event_payload,
    _first_error,
    _infer_agent,
    _input_user_text,
    _iter_trace_files,
    _manifest_by_trace_path,
    _parts_text,
    _preview,
    _read_jsonl_records,
    _record_host,
    _record_model,
    _record_response_text,
    _record_usage,
    _request_user_text,
    _response_events,
    _response_text,
    _summarize_session,
    dashboard_trace_snapshot,
    list_trace_agents,
    list_trace_sessions,
    load_trace_session,
    read_dashboard_template,
    rel_path_for_session_id,
    session_id_for_rel_path,
    trace_path_for_session_id,
)
from claude_tap.live import LiveViewerServer
from claude_tap.trace import TraceWriter


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False, separators=(",", ":")) for record in records) + "\n",
        encoding="utf-8",
    )


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


def test_dashboard_lists_sessions_across_agents(tmp_path: Path) -> None:
    claude_trace = tmp_path / "2026-05-20" / "trace_080000.jsonl"
    agy_trace = tmp_path / "2026-05-20" / "trace_090000.jsonl"
    _write_jsonl(claude_trace, [_anthropic_record()])
    _write_jsonl(agy_trace, [_antigravity_record()])

    sessions = list_trace_sessions(tmp_path)

    assert [session["agent"] for session in sessions] == ["Antigravity", "Claude Code"]
    assert sessions[0]["first_user"] == "What model are you?"
    assert sessions[0]["last_response"] == "I am Sonnet."
    assert sessions[1]["input_tokens"] == 42
    assert sessions[1]["output_tokens"] == 9

    agents = list_trace_agents(tmp_path)
    assert [(agent["label"], agent["sessions"]) for agent in agents] == [("Antigravity", 1), ("Claude Code", 1)]


def test_dashboard_first_message_uses_first_user_prompt(tmp_path: Path) -> None:
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

    summary = list_trace_sessions(tmp_path)[0]

    assert summary["first_user"] == "What is this project?"


def test_dashboard_loads_session_by_id(tmp_path: Path) -> None:
    trace_path = tmp_path / "2026-05-20" / "trace_080000.jsonl"
    _write_jsonl(trace_path, [_anthropic_record()])
    session_id = list_trace_sessions(tmp_path)[0]["id"]

    payload = load_trace_session(tmp_path, session_id)

    assert payload is not None
    assert payload["session"]["rel_trace_path"] == "2026-05-20/trace_080000.jsonl"
    assert payload["records"][0]["request_id"] == "req_claude"


def test_dashboard_rejects_unsafe_or_missing_session_ids(tmp_path: Path) -> None:
    template = read_dashboard_template()
    assert "session-list" in template
    assert "lang-select" in template
    assert "DASHBOARD_I18N" in template
    assert 'data-i18n="table_first_message"' in template
    assert rel_path_for_session_id("not-valid-@@") is None
    assert rel_path_for_session_id(session_id_for_rel_path("/tmp/trace.jsonl")) is None
    assert rel_path_for_session_id(session_id_for_rel_path("../trace.jsonl")) is None
    assert trace_path_for_session_id(tmp_path, "not-valid-@@") is None

    trace_path = tmp_path / "2026-05-20" / "trace_080000.txt"
    trace_path.parent.mkdir()
    trace_path.write_text("{}", encoding="utf-8")
    session_id = session_id_for_rel_path("2026-05-20/trace_080000.txt")
    assert trace_path_for_session_id(tmp_path, session_id) is None
    assert load_trace_session(tmp_path, "not-valid-@@") is None


def test_dashboard_handles_empty_malformed_and_manifest_history(tmp_path: Path) -> None:
    missing_dir = tmp_path / "missing"
    assert dashboard_trace_snapshot(missing_dir) == {}
    assert _iter_trace_files(missing_dir) == []
    assert _read_jsonl_records(missing_dir / "trace_missing.jsonl") == []
    assert _manifest_by_trace_path(tmp_path) == {}

    trace_path = tmp_path / "2026-05-20" / "trace_080000.jsonl"
    trace_path.parent.mkdir()
    trace_path.write_text(
        '\nnot-json\n[]\n{"request_id":"ok"}\n',
        encoding="utf-8",
    )
    assert _read_jsonl_records(trace_path) == [{"request_id": "ok"}]

    manifest_path = tmp_path / ".cloudtap-manifest.json"
    manifest_path.write_text("[]", encoding="utf-8")
    assert _manifest_by_trace_path(tmp_path) == {}
    manifest_path.write_text(
        json.dumps(
            {
                "traces": [
                    "bad",
                    {"files": [1, "2026-05-20/trace_080000.log"]},
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
    manifest = _manifest_by_trace_path(tmp_path)
    assert manifest["2026-05-20/trace_080000.jsonl"]["client"] == "kimi"

    summary = _summarize_session(
        output_dir=tmp_path,
        trace_path=trace_path,
        rel_path="2026-05-20/trace_080000.jsonl",
        records=[],
        manifest_entry=manifest["2026-05-20/trace_080000.jsonl"],
        is_current=False,
    )
    assert summary["status"] == "empty"
    assert summary["agent"] == "Kimi"


def test_dashboard_parses_provider_fallbacks(tmp_path: Path) -> None:
    html_trace = tmp_path / "2026-05-20" / "trace_090000.jsonl"
    html_trace.parent.mkdir()
    _write_jsonl(html_trace, [_antigravity_record()])
    html_trace.with_suffix(".html").write_text("<!doctype html>", encoding="utf-8")
    html_trace.with_suffix(".log").write_text("log", encoding="utf-8")

    summary = list_trace_sessions(tmp_path, current_trace_path=html_trace)[0]
    assert summary["status"] == "active"
    assert summary["html_path"].endswith("trace_090000.html")
    assert summary["log_path"].endswith("trace_090000.log")
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


def test_dashboard_preview_skips_auxiliary_auth_records(tmp_path: Path) -> None:
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

    summary = list_trace_sessions(tmp_path)[0]

    assert summary["first_user"] == "Gemini dashboard prompt"
    assert summary["last_response"] == "Gemini dashboard response."


@pytest.mark.asyncio
async def test_dashboard_server_serves_session_api_and_html(tmp_path: Path) -> None:
    trace_path = tmp_path / "2026-05-20" / "trace_080000.jsonl"
    html_path = trace_path.with_suffix(".html")
    no_html_trace_path = tmp_path / "2026-05-20" / "trace_081500.jsonl"
    _write_jsonl(trace_path, [_anthropic_record()])
    _write_jsonl(no_html_trace_path, [_anthropic_record(turn=2)])
    html_path.write_text("<!doctype html><title>trace</title>", encoding="utf-8")

    server = LiveViewerServer(tmp_path / "dashboard.jsonl", port=0, output_dir=tmp_path, dashboard_mode=True)
    port = await server.start()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/") as resp:
                assert resp.status == 200
                html = await resp.text()
                assert "session-list" in html

            async with session.get(f"http://127.0.0.1:{port}/api/sessions") as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert len(payload["sessions"]) == 2
                session_id = next(item["id"] for item in payload["sessions"] if item["html_path"])
                no_html_session_id = next(item["id"] for item in payload["sessions"] if not item["html_path"])

            async with session.get(f"http://127.0.0.1:{port}/api/agents") as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert payload["agents"][0]["label"] == "Claude Code"

            async with session.get(f"http://127.0.0.1:{port}/api/sessions/{session_id}/records") as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert payload["records"][0]["request_id"] == "req_claude"

            async with session.get(f"http://127.0.0.1:{port}/api/sessions/{session_id}/html") as resp:
                assert resp.status == 200
                html = await resp.text()
                assert "<title>trace</title>" in html

            async with session.get(f"http://127.0.0.1:{port}/api/sessions/bad/records") as resp:
                assert resp.status == 404

            async with session.get(f"http://127.0.0.1:{port}/api/sessions/bad/html") as resp:
                assert resp.status == 404

            async with session.get(f"http://127.0.0.1:{port}/api/sessions/{no_html_session_id}/html") as resp:
                assert resp.status == 404
                assert await resp.text() == "HTML viewer not generated yet"
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_dashboard_server_no_output_dir_and_sse_events(tmp_path: Path) -> None:
    server = LiveViewerServer(tmp_path / "trace_current.jsonl", port=0, dashboard_mode=True)
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

            async with session.get(f"http://127.0.0.1:{port}/api/sessions/anything/html") as resp:
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


def test_dashboard_current_session_id_handles_output_dir_boundaries(tmp_path: Path) -> None:
    current = tmp_path / "2026-05-20" / "trace_current.jsonl"
    server = LiveViewerServer(current, output_dir=tmp_path)
    assert server._current_session_id() == session_id_for_rel_path("2026-05-20/trace_current.jsonl")

    assert LiveViewerServer(current)._current_session_id() is None
    assert LiveViewerServer(tmp_path.parent / "trace_outside.jsonl", output_dir=tmp_path)._current_session_id() is None


@pytest.mark.asyncio
async def test_trace_writer_adds_capture_metadata(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    writer = TraceWriter(trace_path, metadata={"client": "codex", "proxy_mode": "forward"})
    try:
        await writer.write(_anthropic_record())
    finally:
        writer.close()

    record = json.loads(trace_path.read_text(encoding="utf-8"))
    assert record["capture"] == {"client": "claude", "proxy_mode": "reverse"}

    writer = TraceWriter(trace_path, metadata={"client": "codex", "proxy_mode": "forward"})
    try:
        await writer.write({"request": {"body": {}}, "response": {"body": {}}})
    finally:
        writer.close()

    records = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert records[-1]["capture"] == {"client": "codex", "proxy_mode": "forward"}
