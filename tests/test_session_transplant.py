"""Tests for trace -> Claude Code resume session transplant."""

from __future__ import annotations

import itertools
import json

import pytest

from claude_tap.export import export_main
from claude_tap.import_resume import import_resume_main
from claude_tap.session_transplant import (
    RESUME_TARGETS,
    TransplantEnv,
    build_session_jsonl,
    conversation_to_events,
    extract_conversation,
    get_resume_target,
    has_transplantable_conversation,
    install_resume_session,
    parse_jsonl_conversation,
    project_slug,
)


def _det_env(**kwargs) -> TransplantEnv:
    counter = itertools.count(1)
    defaults = dict(
        cwd=r"C:\work\proj",
        session_id="SID",
        new_uuid=lambda: f"u{next(counter):04d}",
    )
    defaults.update(kwargs)
    return TransplantEnv(**defaults)


def _tool_conversation() -> list[dict]:
    return [
        {"role": "user", "content": "fix the bug"},
        {
            "role": "assistant",
            "id": "msg_1",
            "content": [
                {"type": "thinking", "thinking": "let me look"},
                {"type": "text", "text": "Checking two files."},
                {"type": "tool_use", "id": "tu_a", "name": "Read", "input": {"f": "a"}},
                {"type": "tool_use", "id": "tu_b", "name": "Read", "input": {"f": "b"}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "tu_a", "content": "A body"},
                {"type": "tool_result", "tool_use_id": "tu_b", "content": "B body"},
            ],
        },
        {"role": "assistant", "id": "msg_2", "content": [{"type": "text", "text": "Done."}]},
    ]


def test_project_slug_collapses_non_alphanumeric() -> None:
    assert project_slug(r"G:\project\claude-tap") == "G--project-claude-tap"
    assert project_slug(r"C:\Users\23638") == "C--Users-23638"
    # dots and mixed separators all collapse to single dashes
    assert project_slug("/home/me/app.v2") == "-home-me-app-v2"


def test_extract_conversation_picks_fullest_and_appends_response() -> None:
    records = [
        {
            "turn": 1,
            "request": {
                "path": "/v1/messages",
                "body": {"model": "m", "messages": [{"role": "user", "content": "hi"}]},
            },
            "response": {"body": {"content": [{"type": "text", "text": "early"}]}},
        },
        {
            "turn": 2,
            "request": {
                "path": "/v1/messages",
                "body": {
                    "model": "m",
                    "messages": [
                        {"role": "user", "content": "hi"},
                        {"role": "assistant", "content": [{"type": "text", "text": "early"}]},
                        {"role": "user", "content": "more"},
                    ],
                },
            },
            "response": {"body": {"content": [{"type": "text", "text": "final answer"}]}},
        },
    ]

    convo = extract_conversation(records)

    # fullest request (turn 2, 3 messages) + appended final response = 4 messages
    assert len(convo) == 4
    assert convo[-1] == {"role": "assistant", "content": [{"type": "text", "text": "final answer"}]}


def test_extract_conversation_raises_without_anthropic_traffic() -> None:
    with pytest.raises(ValueError, match="no Anthropic conversation"):
        extract_conversation([{"turn": 1, "request": {"path": "/v1/responses", "body": {"input": "x"}}}])


def test_has_transplantable_conversation_guards_by_provider() -> None:
    anthropic = {"request": {"path": "/v1/messages", "body": {"messages": [{"role": "user", "content": "hi"}]}}}
    gemini = {"request": {"path": "/v1internal:streamGenerateContent", "body": {"contents": []}}}
    count_only = {"request": {"path": "/v1/messages/count_tokens", "body": {"messages": [{"role": "user"}]}}}

    assert has_transplantable_conversation([anthropic]) is True
    assert has_transplantable_conversation([gemini]) is False
    assert has_transplantable_conversation([count_only]) is False
    assert has_transplantable_conversation([]) is False


def test_extract_conversation_ignores_count_tokens_probe() -> None:
    # a later count_tokens probe has a longer messages[] but no assistant turn;
    # it must not win selection and drop the real final response.
    records = [
        {
            "turn": 1,
            "request": {
                "path": "/v1/messages",
                "body": {"messages": [{"role": "user", "content": "do it"}]},
            },
            "response": {"body": {"content": [{"type": "text", "text": "final answer"}]}},
        },
        {
            "turn": 2,
            "request": {
                "path": "/v1/messages/count_tokens?beta=true",
                "body": {
                    "messages": [
                        {"role": "user", "content": "do it"},
                        {"role": "assistant", "content": [{"type": "text", "text": "final answer"}]},
                    ]
                },
            },
            "response": {"body": {}},
        },
    ]

    convo = extract_conversation(records)

    assert convo[-1] == {"role": "assistant", "content": [{"type": "text", "text": "final answer"}]}


def test_extract_conversation_drops_unsigned_thinking_in_appended_tail() -> None:
    record = {
        "turn": 1,
        "request": {"path": "/v1/messages", "body": {"messages": [{"role": "user", "content": "go"}]}},
        "response": {
            "body": {
                "content": [
                    {"type": "thinking", "thinking": "unsigned musings"},
                    {"type": "text", "text": "answer"},
                ]
            }
        },
    }

    tail = extract_conversation([record])[-1]["content"]

    assert {b["type"] for b in tail} == {"text"}


def test_extract_conversation_keeps_signed_thinking_in_appended_tail() -> None:
    record = {
        "turn": 1,
        "request": {"path": "/v1/messages", "body": {"messages": [{"role": "user", "content": "go"}]}},
        "response": {
            "body": {
                "content": [
                    {"type": "thinking", "thinking": "signed musings", "signature": "abc"},
                    {"type": "text", "text": "answer"},
                ]
            }
        },
    }

    tail = extract_conversation([record])[-1]["content"]

    assert {b["type"] for b in tail} == {"thinking", "text"}


def test_resume_target_registry_has_claude_and_rejects_unknown() -> None:
    assert "claude" in RESUME_TARGETS
    assert "codex" in RESUME_TARGETS
    assert get_resume_target("claude").label == "Claude Code"
    assert get_resume_target("codex").label == "Codex CLI"
    with pytest.raises(ValueError, match="unknown resume target"):
        get_resume_target("gemini")


def test_install_resume_session_writes_codex_session_and_index(tmp_path) -> None:
    installed = install_resume_session(
        _tool_conversation(),
        r"C:\work\proj",
        target="codex",
        home=tmp_path,
        session_id="abc123",
        title="Portable handoff",
    )

    assert installed.target == "codex"
    assert installed.resume_command == "claude-tap --tap-client codex -- resume abc123"
    assert installed.path.name.endswith("-abc123.jsonl")
    assert installed.path.parent.parent.parent.parent == tmp_path / ".codex" / "sessions"
    lines = [json.loads(line) for line in installed.path.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["type"] == "session_meta"
    assert lines[0]["payload"]["id"] == "abc123"
    assert lines[0]["payload"]["thread_name"] == "Portable handoff"
    assert any(line.get("payload", {}).get("role") == "user" for line in lines if line.get("type") == "response_item")
    assert any(
        line.get("payload", {}).get("role") == "assistant" for line in lines if line.get("type") == "response_item"
    )

    index = tmp_path / ".codex" / "session_index.jsonl"
    assert index.exists()
    entries = [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines()]
    assert entries[-1]["id"] == "abc123"
    assert entries[-1]["thread_name"] == "Portable handoff"


def test_install_resume_session_rejects_unknown_target(tmp_path) -> None:
    with pytest.raises(ValueError, match="unknown resume target"):
        install_resume_session([{"role": "user", "content": "x"}], r"C:\p", target="gemini", home=tmp_path)


def test_install_resume_session_rejects_traversal_session_id(tmp_path) -> None:
    with pytest.raises(ValueError, match="invalid session id"):
        install_resume_session([{"role": "user", "content": "x"}], r"C:\p", home=tmp_path, session_id="../escape")
    # nothing should have been written outside the project store
    assert not list(tmp_path.rglob("*.jsonl"))


def test_tool_results_thread_to_their_tool_use() -> None:
    events = conversation_to_events(_tool_conversation(), _det_env())

    by_uuid = {e["uuid"]: e for e in events}
    tool_use_uuid: dict[str, str] = {}
    tool_result_parent: dict[str, str] = {}
    for e in events:
        for block in e["message"]["content"]:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tool_use_uuid[block["id"]] = e["uuid"]
            if isinstance(block, dict) and block.get("type") == "tool_result":
                tool_result_parent[block["tool_use_id"]] = e["parentUuid"]

    # each tool_result event's parent is exactly the matching tool_use event
    assert tool_result_parent["tu_a"] == tool_use_uuid["tu_a"]
    assert tool_result_parent["tu_b"] == tool_use_uuid["tu_b"]
    # every parent reference resolves (or is the root)
    for e in events:
        assert e["parentUuid"] is None or e["parentUuid"] in by_uuid


def test_assistant_blocks_share_message_id_and_explode_per_block() -> None:
    events = conversation_to_events(_tool_conversation(), _det_env())
    assistant_events = [e for e in events if e["type"] == "assistant"]

    # first assistant message had 4 content blocks -> 4 events, one block each
    first_msg = [e for e in assistant_events if e["requestId"] == "msg_1"]
    assert len(first_msg) == 4
    assert all(len(e["message"]["content"]) == 1 for e in first_msg)
    assert {e["message"]["id"] for e in first_msg} == {"msg_1"}


def test_round_trip_build_parse_reaches_a_stable_fixpoint() -> None:
    # parse normalizes string content into a text block (both are valid API
    # shapes); the meaningful guarantee is that build/parse then stabilizes, so
    # a re-homed session reproduces the same messages[] Claude Code will resend.
    convo = _tool_conversation()
    parsed = parse_jsonl_conversation(build_session_jsonl(convo, _det_env()))
    again = parse_jsonl_conversation(build_session_jsonl(parsed, _det_env()))

    assert parsed == again
    # the original user prompt and the tool round survive intact
    assert parsed[0] == {"role": "user", "content": [{"type": "text", "text": "fix the bug"}]}
    assert {b["type"] for b in parsed[1]["content"]} == {"thinking", "text", "tool_use"}
    tool_results = [b for b in parsed[2]["content"] if b["type"] == "tool_result"]
    assert {b["tool_use_id"] for b in tool_results} == {"tu_a", "tu_b"}


def test_build_session_jsonl_sets_leaf_and_session_header() -> None:
    doc = build_session_jsonl(_tool_conversation(), _det_env())
    lines = [json.loads(line) for line in doc.splitlines()]

    assert lines[0] == {"type": "mode", "mode": "normal", "sessionId": "SID"}
    assert lines[1]["type"] == "permission-mode"
    last = lines[-1]
    assert last["type"] == "last-prompt"
    conversation_uuids = [line["uuid"] for line in lines if line.get("type") in ("user", "assistant")]
    assert last["leafUuid"] == conversation_uuids[-1]


def test_export_claude_resume_format(tmp_path, capsys) -> None:
    trace_path = tmp_path / "trace.jsonl"
    record = {
        "turn": 1,
        "request": {"path": "/v1/messages", "body": {"model": "m", "messages": [{"role": "user", "content": "hello"}]}},
        "response": {"body": {"content": [{"type": "text", "text": "hi there"}]}},
    }
    trace_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    out_path = tmp_path / "transplant.jsonl"

    assert export_main([str(trace_path), "--format", "claude-resume", "-o", str(out_path), "--cwd", r"C:\b"]) == 0

    messages = parse_jsonl_conversation(out_path.read_text(encoding="utf-8"))
    assert messages == [
        {"role": "user", "content": [{"type": "text", "text": "hello"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "hi there"}]},
    ]


def test_export_resume_format_alias(tmp_path, capsys) -> None:
    trace_path = tmp_path / "trace.jsonl"
    record = {
        "turn": 1,
        "request": {"path": "/v1/messages", "body": {"model": "m", "messages": [{"role": "user", "content": "hi"}]}},
        "response": {"body": {"content": [{"type": "text", "text": "hello"}]}},
    }
    trace_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    out_path = tmp_path / "portable.jsonl"

    assert export_main([str(trace_path), "--format", "resume", "-o", str(out_path)]) == 0

    assert parse_jsonl_conversation(out_path.read_text(encoding="utf-8"))[-1]["content"][0]["text"] == "hello"


def test_install_resume_session_writes_under_project_slug(tmp_path) -> None:
    installed = install_resume_session(
        _tool_conversation(),
        r"C:\work\proj",
        home=tmp_path,
        session_id="abc123",
    )

    expected = tmp_path / ".claude" / "projects" / "C--work-proj" / "abc123.jsonl"
    assert installed.path == expected
    assert expected.exists()
    assert installed.resume_command == "claude-tap --resume abc123"
    assert installed.message_count == 4


def test_import_resume_main_installs_and_warns_on_cwd_divergence(tmp_path, capsys) -> None:
    # a transplant file captured under a different cwd
    src = tmp_path / "transplant.jsonl"
    env = _det_env(cwd=r"D:\other\place")
    src.write_text(build_session_jsonl(_tool_conversation(), env), encoding="utf-8")

    target = tmp_path / "dest"
    target.mkdir()
    rc = import_resume_main([str(src), "--cwd", str(target), "--home", str(tmp_path / "home"), "--session-id", "sid9"])

    assert rc == 0
    out = capsys.readouterr()
    assert "Warning" in out.err and r"D:\other\place" in out.err
    installed = tmp_path / "home" / ".claude" / "projects" / project_slug(str(target)) / "sid9.jsonl"
    assert installed.exists()
    assert "claude-tap --resume sid9" in out.out
    # the re-homed session reproduces the original conversation (string content
    # normalized to a text block by the parse round-trip)
    expected = parse_jsonl_conversation(build_session_jsonl(_tool_conversation(), _det_env()))
    assert parse_jsonl_conversation(installed.read_text(encoding="utf-8")) == expected


def test_import_resume_main_no_warning_for_same_dir_written_differently(tmp_path, capsys) -> None:
    import os

    target = tmp_path / "dest"
    target.mkdir()
    src = tmp_path / "transplant.jsonl"
    src.write_text(build_session_jsonl(_tool_conversation(), _det_env(cwd=str(target))), encoding="utf-8")

    # same directory, expressed with a redundant "." segment -> must not warn
    rc = import_resume_main([str(src), "--cwd", os.path.join(str(target), "."), "--home", str(tmp_path / "home")])

    assert rc == 0
    assert "Warning" not in capsys.readouterr().err


def test_import_resume_main_rejects_empty_source(tmp_path, capsys) -> None:
    src = tmp_path / "empty.jsonl"
    src.write_text('{"type": "mode", "mode": "normal"}\n', encoding="utf-8")

    assert import_resume_main([str(src), "--home", str(tmp_path / "home")]) == 1
    assert "no user/assistant messages" in capsys.readouterr().err
