"""Tests for trace export formats."""

from __future__ import annotations

import base64
import json

import pytest

from claude_tap.export import export_main


def _write_trace(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    record = {
        "timestamp": "2026-04-28T12:00:00",
        "turn": 1,
        "duration_ms": 123,
        "request": {
            "body": {
                "model": "claude-sonnet-4-6",
                "messages": [{"role": "user", "content": "hello from trace"}],
            }
        },
        "response": {
            "body": {
                "content": [{"type": "text", "text": "hello from assistant"}],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }
        },
    }
    trace_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    return trace_path


def test_export_html_inferred_from_output_suffix(tmp_path, capsys) -> None:
    trace_path = _write_trace(tmp_path)
    html_path = tmp_path / "trace.html"

    assert export_main([str(trace_path), "-o", str(html_path)]) == 0

    html = html_path.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in html
    assert "EMBEDDED_TRACE_DATA" in html
    assert "hello from trace" in html
    assert f"Exported 1 turns to {html_path}" in capsys.readouterr().out


def test_export_html_includes_iframe_embed_query_support(tmp_path, capsys) -> None:
    trace_path = _write_trace(tmp_path)
    html_path = tmp_path / "trace.html"

    assert export_main([str(trace_path), "-o", str(html_path)]) == 0

    html = html_path.read_text(encoding="utf-8")
    assert "parseEmbedQueryOptions" in html
    assert "embed-hide-header" in html
    assert "hideControls" in html
    assert "density') === 'compact'" in html
    assert f"Exported 1 turns to {html_path}" in capsys.readouterr().out


def test_export_html_format_defaults_to_trace_html_path(tmp_path, capsys) -> None:
    trace_path = _write_trace(tmp_path)
    html_path = trace_path.with_suffix(".html")

    assert export_main([str(trace_path), "--format", "html"]) == 0

    assert html_path.exists()
    assert "hello from assistant" in html_path.read_text(encoding="utf-8")
    assert f"Exported 1 turns to {html_path}" in capsys.readouterr().out


def test_export_markdown_maps_responses_cached_tokens(tmp_path, capsys) -> None:
    trace_path = tmp_path / "trace.jsonl"
    record = {
        "timestamp": "2026-05-09T00:00:00",
        "turn": 1,
        "duration_ms": 12,
        "request": {
            "body": {
                "model": "gpt-5.4",
                "input": [{"role": "user", "content": "hi"}],
            }
        },
        "response": {
            "body": {
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "hello"}]}],
                "usage": {
                    "input_tokens": 11767,
                    "input_tokens_details": {"cached_tokens": 11648},
                    "output_tokens": 6,
                    "total_tokens": 11773,
                },
            }
        },
    }
    trace_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    assert export_main([str(trace_path), "--format", "markdown"]) == 0

    output = capsys.readouterr().out
    assert "- **Cache read tokens**: 11,648" in output
    assert "*Tokens: in=11,767 / out=6 / cache_read=11,648*" in output


def test_export_help_mentions_html(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        export_main(["--help"])

    assert exc_info.value.code == 0
    help_text = capsys.readouterr().out
    assert "{markdown,json,html,compact,prompt-md}" in help_text
    assert "for HTML" in help_text


def test_export_compact_trace_is_standalone_and_html_renderable(tmp_path, capsys) -> None:
    from claude_tap.compact_trace import load_compact_trace

    trace_path = tmp_path / "trace.jsonl"
    compact_path = tmp_path / "trace.ctap.json"
    html_path = tmp_path / "trace.html"
    repeated_tools = [{"type": "function", "name": "shell", "description": "tool schema " * 200}]
    repeated_input = {
        "role": "user",
        "content": [{"type": "input_text", "text": "shared compact input payload " * 200}],
    }
    records = []
    for turn in range(1, 4):
        records.append(
            {
                "timestamp": f"2026-05-30T10:00:0{turn}+00:00",
                "turn": turn,
                "request": {
                    "body": {
                        "model": "gpt-5.5",
                        "instructions": "shared instructions " * 200,
                        "tools": repeated_tools,
                        "input": [repeated_input, {"role": "user", "content": f"turn {turn}"}],
                    }
                },
                "response": {
                    "body": {
                        "output": [{"type": "message", "content": [{"type": "output_text", "text": f"ok {turn}"}]}]
                    }
                },
            }
        )
    raw_jsonl = "\n".join(json.dumps(record, ensure_ascii=False, separators=(",", ":")) for record in records) + "\n"
    trace_path.write_text(raw_jsonl, encoding="utf-8")

    assert export_main([str(trace_path), "--format", "compact", "-o", str(compact_path)]) == 0

    compact_text = compact_path.read_text(encoding="utf-8")
    assert "__claude_tap_compact_trace__" in compact_text
    assert "__claude_tap_blob_ref__" in compact_text
    assert "shared compact input payload" in compact_text
    assert compact_text.count('"role":"user","content":[{"type":"input_text","text":"shared compact') == 1
    assert len(compact_text.encode("utf-8")) < len(raw_jsonl.encode("utf-8")) * 0.5
    assert load_compact_trace(compact_text) == records

    assert export_main([str(compact_path), "-o", str(html_path)]) == 0
    html = html_path.read_text(encoding="utf-8")
    assert "EMBEDDED_TRACE_COMPACT_DATA" in html
    assert "turn 3" in html
    assert "shared compact input payload" in html
    assert f"Exported 3 turns to {compact_path}" in capsys.readouterr().out


def _bedrock_frame(event: dict) -> str:
    payload = json.dumps(event).encode("utf-8")
    return json.dumps({"bytes": base64.b64encode(payload).decode("ascii")})


def test_export_json_tolerates_null_request_body_and_stream_text_response(tmp_path, capsys) -> None:
    trace_path = tmp_path / "trace.jsonl"
    json_path = tmp_path / "trace.export.json"
    records = [
        {
            "timestamp": "2026-04-28T12:00:00",
            "turn": 2,
            "request": {"method": "GET", "path": "/inference-profiles", "body": None},
            "response": {"status": 200, "body": {"profiles": []}},
        },
        {
            "timestamp": "2026-04-28T12:00:01",
            "turn": 1,
            "request": {
                "method": "POST",
                "path": "/model/test/invoke-with-response-stream",
                "body": {"model": "claude-haiku-4-5", "messages": [{"role": "user", "content": "hello"}]},
            },
            "response": {
                "status": 200,
                "body": "".join(
                    [
                        _bedrock_frame(
                            {
                                "type": "message_start",
                                "message": {"id": "msg_1", "type": "message", "role": "assistant", "content": []},
                            }
                        ),
                        _bedrock_frame(
                            {
                                "type": "content_block_start",
                                "index": 0,
                                "content_block": {"type": "text", "text": ""},
                            }
                        ),
                        _bedrock_frame(
                            {
                                "type": "content_block_delta",
                                "index": 0,
                                "delta": {"type": "text_delta", "text": "hello from stream"},
                            }
                        ),
                        _bedrock_frame(
                            {
                                "type": "message_delta",
                                "delta": {"stop_reason": "end_turn"},
                                "usage": {"input_tokens": 4, "output_tokens": 3},
                            }
                        ),
                    ]
                ),
            },
        },
    ]
    trace_path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")

    assert export_main([str(trace_path), "--format", "json", "-o", str(json_path)]) == 0

    exported = json.loads(json_path.read_text(encoding="utf-8"))
    assert [entry["turn"] for entry in exported] == [1, 2]
    assert exported[0]["response"]["content"] == [{"type": "text", "text": "hello from stream"}]
    assert exported[0]["response"]["usage"] == {"input_tokens": 4, "output_tokens": 3}
    assert exported[1]["model"] is None
    assert exported[1]["messages"] == []
    assert f"Exported 2 turns to {json_path}" in capsys.readouterr().out


def test_export_accepts_positional_sqlite_session_id(trace_db, tmp_path, capsys) -> None:
    from claude_tap.trace_store import get_trace_store

    store = get_trace_store()
    session_id = store.create_session(client="claude", proxy_mode="reverse")
    store.append_record(
        session_id,
        {
            "timestamp": "2026-05-24T10:00:00+00:00",
            "turn": 1,
            "request": {
                "body": {
                    "model": "claude-sonnet-4-6",
                    "messages": [{"role": "user", "content": "hello from session"}],
                }
            },
            "response": {
                "body": {
                    "content": [{"type": "text", "text": "stored response"}],
                    "usage": {"input_tokens": 3, "output_tokens": 2},
                }
            },
        },
    )
    json_path = tmp_path / "session-export.json"

    assert export_main([session_id, "--format", "json", "-o", str(json_path)]) == 0

    exported = json.loads(json_path.read_text(encoding="utf-8"))
    assert exported[0]["messages"] == [{"role": "user", "content": "hello from session"}]
    assert exported[0]["response"]["content"] == [{"type": "text", "text": "stored response"}]
    assert f"Exported 1 turns to {json_path}" in capsys.readouterr().out


def test_export_session_html_does_not_materialize_jsonl_file(trace_db, tmp_path, capsys, monkeypatch) -> None:
    from claude_tap.trace_store import TraceStore, get_trace_store

    store = get_trace_store()
    session_id = store.create_session(client="codex", proxy_mode="reverse")
    store.append_record(
        session_id,
        {
            "timestamp": "2026-05-30T10:00:00+00:00",
            "turn": 1,
            "request": {
                "method": "WEBSOCKET",
                "path": "/v1/responses",
                "body": {
                    "model": "gpt-5.5",
                    "instructions": "system instructions " * 100,
                    "tools": [{"type": "function", "name": "shell", "description": "tool " * 200}],
                    "input": [{"role": "user", "content": "hello"}],
                },
            },
            "response": {
                "status": 101,
                "body": {
                    "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
                },
            },
        },
    )

    def fail_export_jsonl(self, session_id):  # noqa: ANN001, ARG001
        raise AssertionError("HTML export should not materialize a full JSONL file first")

    monkeypatch.setattr(TraceStore, "export_jsonl", fail_export_jsonl)
    html_path = tmp_path / "session.html"

    assert export_main([session_id, "--format", "html", "-o", str(html_path)]) == 0

    html = html_path.read_text(encoding="utf-8")
    assert "EMBEDDED_TRACE_COMPACT_DATA" in html
    assert "gpt-5.5" in html
    assert "Exported 1 turns" in capsys.readouterr().out


def test_export_prompt_markdown_matches_prompt_snapshot_format(tmp_path, capsys) -> None:
    trace_path = tmp_path / "trace.jsonl"
    prompt_path = tmp_path / "prompt.md"
    record = {
        "timestamp": "2026-05-21T10:00:00+00:00",
        "request_id": "req_1",
        "turn": 1,
        "duration_ms": 1,
        "request": {
            "method": "POST",
            "path": "/v1/messages?beta=true",
            "headers": {},
            "body": {
                "model": "claude-opus",
                "system": [{"type": "text", "text": "main system"}],
                "messages": [{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
                "tools": [
                    {
                        "name": "Bash",
                        "description": "Run shell commands",
                        "input_schema": {"type": "object", "properties": {"cmd": {"type": "string"}}},
                    }
                ],
            },
        },
        "response": {"status": 200, "headers": {}, "body": {}},
    }
    trace_path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")

    assert export_main([str(trace_path), "--format", "prompt-md", "-o", str(prompt_path)]) == 0

    assert prompt_path.read_text(encoding="utf-8") == (
        "# System Prompt\n\n"
        "main system\n\n"
        "# User Message\n\n"
        "hello\n\n"
        "# Tools\n\n"
        "## Bash\n\n"
        "Run shell commands\n\n"
        "```json\n"
        '{\n  "type": "object",\n  "properties": {\n    "cmd": {\n      "type": "string"\n    }\n  }\n}\n'
        "```\n"
    )
    assert f"Exported 1 turns to {prompt_path}" in capsys.readouterr().out


def test_export_prompt_markdown_accepts_sqlite_session(trace_db, tmp_path) -> None:
    from claude_tap.trace_store import get_trace_store

    store = get_trace_store()
    session_id = store.create_session(client="codex", proxy_mode="reverse")
    store.append_record(
        session_id,
        {
            "timestamp": "2026-05-21T10:00:00+00:00",
            "request_id": "req_1",
            "turn": 1,
            "duration_ms": 1,
            "request": {
                "method": "POST",
                "path": "/v1/responses",
                "headers": {},
                "body": {
                    "model": "gpt-5",
                    "instructions": "system instructions",
                    "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
                    "tools": [{"type": "function", "name": "shell", "description": "Run shell"}],
                },
            },
            "response": {"status": 200, "headers": {}, "body": {}},
        },
    )
    prompt_path = tmp_path / "prompt.md"

    assert export_main([session_id, "--format", "prompt-md", "-o", str(prompt_path)]) == 0

    text = prompt_path.read_text(encoding="utf-8")
    assert "# System Prompt\n\nsystem instructions" in text
    assert "# User Message\n\nhello" in text
    assert "## shell" in text


def test_export_prompt_from_session_also_writes_raw_trace(trace_db, tmp_path, capsys) -> None:
    from claude_tap.cli import _export_prompt_from_session
    from claude_tap.trace_store import get_trace_store

    store = get_trace_store()
    session_id = store.create_session(client="codex", proxy_mode="reverse")
    record = {
        "timestamp": "2026-05-21T10:00:00+00:00",
        "request_id": "req_1",
        "turn": 1,
        "duration_ms": 1,
        "request": {
            "method": "POST",
            "path": "/v1/responses",
            "headers": {},
            "body": {
                "model": "gpt-5",
                "instructions": "system instructions",
                "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
            },
        },
        "response": {"status": 200, "headers": {}, "body": {}},
    }
    store.append_record(session_id, record)
    prompt_path = tmp_path / "prompt.md"

    assert _export_prompt_from_session(store, session_id, str(prompt_path)) == 0

    trace_path = tmp_path / "trace.jsonl"
    assert prompt_path.exists()
    assert trace_path.exists()
    assert trace_path.read_text(encoding="utf-8") == store.export_jsonl(session_id)
    assert json.loads(trace_path.read_text(encoding="utf-8"))["request_id"] == "req_1"
    output = capsys.readouterr().out
    assert f"Prompt snapshot: {prompt_path}" in output
    assert f"Raw trace: {trace_path}" in output


def test_export_prompt_from_session_stdout_and_missing_prompt(trace_db, capsys) -> None:
    from claude_tap.cli import _export_prompt_from_session
    from claude_tap.trace_store import get_trace_store

    store = get_trace_store()
    session_id = store.create_session(client="codex", proxy_mode="reverse")
    store.append_record(
        session_id,
        {
            "timestamp": "2026-05-21T10:00:00+00:00",
            "request_id": "req_1",
            "turn": 1,
            "duration_ms": 1,
            "request": {
                "method": "POST",
                "path": "/v1/responses",
                "headers": {},
                "body": {"model": "gpt-5", "instructions": "system instructions"},
            },
            "response": {"status": 200, "headers": {}, "body": {}},
        },
    )

    assert _export_prompt_from_session(store, session_id, "-") == 0
    assert "# System Prompt\n\nsystem instructions" in capsys.readouterr().out

    empty_session_id = store.create_session(client="codex", proxy_mode="reverse")
    assert _export_prompt_from_session(store, empty_session_id, "-") == 1
    assert "no prompt-bearing request found in trace" in capsys.readouterr().err


def test_export_prompt_from_session_uses_stemmed_trace_name(trace_db, tmp_path) -> None:
    from claude_tap.cli import _export_prompt_from_session
    from claude_tap.trace_store import get_trace_store

    store = get_trace_store()
    session_id = store.create_session(client="codex", proxy_mode="reverse")
    store.append_record(
        session_id,
        {
            "timestamp": "2026-05-21T10:00:00+00:00",
            "request_id": "req_1",
            "turn": 1,
            "duration_ms": 1,
            "request": {
                "method": "POST",
                "path": "/v1/responses",
                "headers": {},
                "body": {"model": "gpt-5", "instructions": "system instructions"},
            },
            "response": {"status": 200, "headers": {}, "body": {}},
        },
    )

    assert _export_prompt_from_session(store, session_id, str(tmp_path / "snapshot.md")) == 0

    assert (tmp_path / "snapshot.md").exists()
    assert (tmp_path / "snapshot.trace.jsonl").exists()
