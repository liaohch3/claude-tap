from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from claude_tap import parse_args
from claude_tap.cli import CLIENT_CONFIGS, run_client
from claude_tap.cursor_metadata import CursorConversationMeta
from claude_tap.cursor_transcript import (
    CursorTranscriptWatcher,
    _cursor_project_slug,
    _load_transcript,
    backfill_cursor_transcript_request_fields,
    build_cursor_transcript_records,
    find_cursor_transcripts,
    import_cursor_transcripts,
    model_from_cursor_args,
)
from tests.schema_types import JsonObject, Map


class _DummyProc:
    def __init__(self) -> None:
        self.pid = 12345
        self.returncode: int | None = None

    async def wait(self) -> int:
        self.returncode = 0
        return 0

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9


def _write_transcript(path: Path, rows: list[JsonObject]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")


def _transcript_path(home: Path, project: str, session_id: str) -> Path:
    return home / ".cursor" / "projects" / project / "agent-transcripts" / session_id / f"{session_id}.jsonl"


def test_cursor_registered_in_client_configs() -> None:
    cfg = CLIENT_CONFIGS["cursor"]
    assert cfg.cmd == "cursor-agent"
    assert cfg.default_target == "https://api2.cursor.sh"
    assert cfg.default_proxy_mode == "forward"
    assert cfg.transcript_only is True


def test_model_from_cursor_args() -> None:
    assert model_from_cursor_args([]) == ""
    assert model_from_cursor_args(["-p", "--trust"]) == ""
    assert model_from_cursor_args(["--model", "grok-code", "-p"]) == "grok-code"
    assert model_from_cursor_args(["--model=auto"]) == ""
    assert model_from_cursor_args(["--model", "auto"]) == ""


def test_parse_args_cursor_defaults_to_launch_and_watch() -> None:
    args = parse_args(["--tap-client", "cursor"])
    assert args.client == "cursor"
    assert args.proxy_mode == "forward"  # unused when transcript_only
    assert args.no_launch is False
    assert args.host == "127.0.0.1"
    assert args.claude_args == []


def test_parse_args_cursor_no_launch_is_watch_only() -> None:
    args = parse_args(["--tap-client", "cursor", "--tap-no-launch"])
    assert args.no_launch is True
    assert args.host == "127.0.0.1"


def test_parse_args_cursor_with_cli_args_launches_agent() -> None:
    args = parse_args(["--tap-client", "cursor", "--", "-p", "--trust", "hello"])
    assert args.no_launch is False
    assert args.claude_args == ["-p", "--trust", "hello"]


def test_parse_args_cursor_rejects_trust_ca() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--tap-client", "cursor", "--tap-trust-ca"])


def test_parse_args_cursor_rejects_export_prompt() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--tap-client", "cursor", "--tap-export-prompt", "-"])


def test_parse_args_cursor_rejects_proxy_mode_override() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--tap-client", "cursor", "--tap-proxy-mode", "reverse"])


@pytest.mark.asyncio
async def test_run_client_cursor_transcript_only_skips_proxy_env(monkeypatch) -> None:
    captured: Map[str, object] = {}
    ca_path = Path("/tmp/test-ca.pem")

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("ALL_PROXY", raising=False)
    monkeypatch.delenv("all_proxy", raising=False)
    monkeypatch.setenv("NO_PROXY", "example.com")
    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/cursor-agent")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(
        43123,
        ["-p", "--trust", "--model", "auto", "hello"],
        client="cursor",
        proxy_mode="forward",
        ca_cert_path=ca_path,
    )

    assert code == 0
    assert captured["cmd"] == ("/tmp/cursor-agent", "-p", "--trust", "--model", "auto", "hello")
    env = captured["env"]
    assert "HTTPS_PROXY" not in env
    assert "HTTP_PROXY" not in env
    assert "NODE_EXTRA_CA_CERTS" not in env
    assert env.get("NO_PROXY") == "example.com"


@pytest.mark.asyncio
async def test_import_cursor_transcripts_appends_viewer_friendly_records(trace_db, tmp_path: Path) -> None:
    cursor_session = "session-123"
    transcript = _transcript_path(tmp_path, "project-one", cursor_session)
    rows = [
        {
            "role": "user",
            "message": {
                "content": [
                    {
                        "type": "text",
                        "text": "<timestamp>now</timestamp>\n<user_query>\nhello cursor\n</user_query>",
                    }
                ]
            },
        },
        {"role": "assistant", "message": {"content": [{"type": "text", "text": "hello back"}]}},
        {"role": "user", "message": {"content": [{"type": "text", "text": "second turn"}]}},
        {"role": "assistant", "message": {"content": [{"type": "text", "text": "second answer"}]}},
    ]
    _write_transcript(transcript, rows)

    from claude_tap.trace_store import get_trace_store

    store = get_trace_store()
    watcher = await import_cursor_transcripts(since=0, home=tmp_path, store=store)
    try:
        assert len(watcher.session_ids) == 1
        session_id = watcher.session_ids[0]
        records = store.load_records(session_id)
        assert len(records) == 2
        assert records[0]["transport"] == "cursor-transcript"
        assert "model" not in records[0]["request"]["body"]
        assert records[0]["request"]["body"]["messages"][0]["content"] == "hello cursor"
        assert records[0]["response"]["body"]["content"][0]["text"] == "hello back"
        assert records[1]["request"]["body"]["messages"][0]["content"] == "second turn"
        assert records[1]["response"]["body"]["content"][0]["text"] == "second answer"
        assert records[0]["capture"]["cursor_transcript_id"] == cursor_session
        assert records[0]["capture"]["cursor_project"] == "project-one"
    finally:
        watcher.close()


@pytest.mark.asyncio
async def test_import_cursor_transcripts_preserves_tool_uses(trace_db, tmp_path: Path) -> None:
    cursor_session = "tool-session"
    transcript = _transcript_path(tmp_path, "project-one", cursor_session)
    rows = [
        {"role": "user", "message": {"content": [{"type": "text", "text": "inspect files"}]}},
        {
            "role": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "I will inspect the workspace."},
                    {
                        "type": "tool_use",
                        "name": "Shell",
                        "input": {"command": "pwd && ls", "working_directory": "/tmp/work"},
                    },
                ]
            },
        },
        {
            "role": "assistant",
            "message": {
                "content": [{"type": "tool_use", "name": "ReadFile", "input": {"path": "/tmp/work/sample.txt"}}]
            },
        },
        {"role": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}},
    ]
    _write_transcript(transcript, rows)

    from claude_tap.trace_store import get_trace_store

    store = get_trace_store()
    watcher = await import_cursor_transcripts(since=0, home=tmp_path, store=store)
    try:
        assert len(watcher.session_ids) == 1
        records = store.load_records(watcher.session_ids[0])
        assert len(records) == 3

        assert records[0]["request"]["path"].endswith("/turn/1/step/1")
        assert records[1]["request"]["path"].endswith("/turn/1/step/2")
        assert records[2]["request"]["path"].endswith("/turn/1/step/3")
        assert records[1]["request"]["body"]["messages"][0]["content"] == "inspect files"

        tools = records[0]["request"]["body"]["tools"]
        assert [tool["name"] for tool in tools] == ["Shell", "ReadFile"]
        assert tools[0]["input_schema"]["properties"]["command"] == {"type": "string"}
        assert tools[0]["input_schema"]["properties"]["working_directory"] == {"type": "string"}
        assert tools[1]["input_schema"]["properties"]["path"] == {"type": "string"}
        assert records[1]["request"]["body"]["tools"] == tools
        assert records[2]["request"]["body"]["tools"] == tools

        content = records[0]["response"]["body"]["content"]
        assert content[0] == {"type": "text", "text": "I will inspect the workspace."}
        assert content[1]["type"] == "tool_use"
        assert content[1]["name"] == "Shell"
        assert content[1]["id"] == "cursor_tool_1_2"

        assert records[1]["response"]["body"]["content"][0]["name"] == "ReadFile"
        assert records[2]["response"]["body"]["content"] == [{"type": "text", "text": "done"}]
    finally:
        watcher.close()


@pytest.mark.asyncio
async def test_cursor_flat_and_nested_same_id_shares_one_session(trace_db, tmp_path: Path) -> None:
    nested = _transcript_path(tmp_path, "project-one", "same-id")
    flat = tmp_path / ".cursor" / "projects" / "project-two" / "agent-transcripts" / "same-id.jsonl"
    rows = [
        {"role": "user", "message": {"content": [{"type": "text", "text": "shared"}]}},
        {"role": "assistant", "message": {"content": [{"type": "text", "text": "reply"}]}},
    ]
    _write_transcript(nested, rows)
    _write_transcript(flat, rows)

    from claude_tap.trace_store import get_trace_store

    store = get_trace_store()
    watcher = CursorTranscriptWatcher(since=0, home=tmp_path, store=store)
    assert await watcher.sync_once() == 1
    try:
        assert len(watcher.session_ids) == 1
        assert len(store.load_records(watcher.session_ids[0])) == 1
        empties = [row for row in store.list_session_rows() if int(row["record_count"] or 0) == 0]
        assert empties == []

        # Newer turns on the other layout append into the same tap session.
        more = rows + [
            {"role": "user", "message": {"content": [{"type": "text", "text": "again"}]}},
            {"role": "assistant", "message": {"content": [{"type": "text", "text": "second"}]}},
        ]
        _write_transcript(flat, more)
        assert await watcher.sync_once() == 1
        assert len(watcher.session_ids) == 1
        records = store.load_records(watcher.session_ids[0])
        assert len(records) == 2
        assert records[1]["request"]["body"]["messages"][0]["content"] == "again"
    finally:
        watcher.close()


@pytest.mark.asyncio
async def test_cursor_transcript_watcher_keeps_conversations_in_separate_sessions(trace_db, tmp_path: Path) -> None:
    first = _transcript_path(tmp_path, "project-a", "conv-a")
    second = _transcript_path(tmp_path, "project-b", "conv-b")
    _write_transcript(
        first,
        [
            {"role": "user", "message": {"content": [{"type": "text", "text": "alpha chat"}]}},
            {"role": "assistant", "message": {"content": [{"type": "text", "text": "alpha reply"}]}},
        ],
    )
    _write_transcript(
        second,
        [
            {"role": "user", "message": {"content": [{"type": "text", "text": "beta chat"}]}},
            {"role": "assistant", "message": {"content": [{"type": "text", "text": "beta reply"}]}},
        ],
    )

    from claude_tap.trace_store import get_trace_store

    store = get_trace_store()
    watcher = CursorTranscriptWatcher(since=0, home=tmp_path, store=store)
    assert await watcher.sync_once() == 2
    try:
        assert len(watcher.session_ids) == 2
        by_text = {}
        for session_id in watcher.session_ids:
            records = store.load_records(session_id)
            assert len(records) == 1
            text = records[0]["request"]["body"]["messages"][0]["content"]
            by_text[text] = records[0]["capture"]["cursor_transcript_id"]
        assert by_text == {"alpha chat": "conv-a", "beta chat": "conv-b"}
    finally:
        watcher.close()


@pytest.mark.asyncio
async def test_cursor_transcript_watcher_incremental_and_dedupe(trace_db, tmp_path: Path) -> None:
    cursor_session = "live-session"
    transcript = _transcript_path(tmp_path, "project-one", cursor_session)
    initial = [
        {"role": "user", "message": {"content": [{"type": "text", "text": "first"}]}},
        {"role": "assistant", "message": {"content": [{"type": "text", "text": "one"}]}},
    ]
    _write_transcript(transcript, initial)

    from claude_tap.trace_store import get_trace_store

    store = get_trace_store()
    watcher = CursorTranscriptWatcher(since=0, home=tmp_path, store=store)

    assert await watcher.sync_once() == 1
    assert await watcher.sync_once() == 0
    assert len(watcher.session_ids) == 1
    db_session = watcher.session_ids[0]

    more = initial + [
        {"role": "user", "message": {"content": [{"type": "text", "text": "second"}]}},
        {"role": "assistant", "message": {"content": [{"type": "text", "text": "two"}]}},
    ]
    _write_transcript(transcript, more)

    assert await watcher.sync_once() == 1
    assert await watcher.sync_once() == 0
    watcher.close()

    records = store.load_records(db_session)
    assert len(records) == 2
    assert records[0]["request"]["body"]["messages"][0]["content"] == "first"
    assert records[1]["request"]["body"]["messages"][0]["content"] == "second"
    paths = [record["request"]["path"] for record in records]
    assert len(paths) == len(set(paths))


@pytest.mark.asyncio
async def test_cursor_transcript_enriches_model_from_local_chat_store(trace_db, tmp_path: Path) -> None:
    cursor_session = "enriched-session"
    transcript = _transcript_path(tmp_path, "project-one", cursor_session)
    _write_transcript(
        transcript,
        [
            {"role": "user", "message": {"content": [{"type": "text", "text": "hi"}]}},
            {"role": "assistant", "message": {"content": [{"type": "text", "text": "yo"}]}},
        ],
    )
    store_db = tmp_path / ".cursor" / "chats" / "ws" / cursor_session / "store.db"
    store_db.parent.mkdir(parents=True)
    import sqlite3

    conn = sqlite3.connect(store_db)
    conn.execute("CREATE TABLE meta (key TEXT, value TEXT)")
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?)",
        ("0", json.dumps({"lastUsedModel": "grok-4.5"}).encode().hex()),
    )
    conn.commit()
    conn.close()

    from claude_tap.trace_store import get_trace_store

    store = get_trace_store()
    watcher = await import_cursor_transcripts(since=0, home=tmp_path, store=store)
    try:
        record = store.load_records(watcher.session_ids[0])[0]
        assert record["request"]["body"]["model"] == "grok-4.5"
        assert record["request"]["body"]["cursor_meta_source"] == "chat-store"
        assert "usage" not in (record.get("response") or {})
    finally:
        watcher.close()


@pytest.mark.asyncio
async def test_cursor_transcript_records_launch_model_hint(trace_db, tmp_path: Path) -> None:
    cursor_session = "model-session"
    transcript = _transcript_path(tmp_path, "project-one", cursor_session)
    _write_transcript(
        transcript,
        [
            {"role": "user", "message": {"content": [{"type": "text", "text": "hi"}]}},
            {"role": "assistant", "message": {"content": [{"type": "text", "text": "yo"}]}},
        ],
    )

    from claude_tap.trace_store import get_trace_store

    store = get_trace_store()
    watcher = await import_cursor_transcripts(since=0, home=tmp_path, store=store, model="grok-code")
    try:
        assert len(watcher.session_ids) == 1
        record = store.load_records(watcher.session_ids[0])[0]
        assert record["request"]["body"]["model"] == "grok-code"
        assert record["response"]["body"]["model"] == "grok-code"
    finally:
        watcher.close()


@pytest.mark.asyncio
async def test_cursor_transcript_auto_model_defers_to_metadata(trace_db, tmp_path: Path) -> None:
    cursor_session = "auto-model-session"
    transcript = _transcript_path(tmp_path, "project-one", cursor_session)
    _write_transcript(
        transcript,
        [
            {"role": "user", "message": {"content": [{"type": "text", "text": "hi"}]}},
            {"role": "assistant", "message": {"content": [{"type": "text", "text": "yo"}]}},
        ],
    )
    store_db = tmp_path / ".cursor" / "chats" / "ws" / cursor_session / "store.db"
    store_db.parent.mkdir(parents=True)
    import sqlite3

    conn = sqlite3.connect(store_db)
    conn.execute("CREATE TABLE meta (key TEXT, value TEXT)")
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?)",
        ("0", json.dumps({"lastUsedModel": "grok-4.5"}).encode().hex()),
    )
    conn.commit()
    conn.close()

    from claude_tap.trace_store import get_trace_store

    store = get_trace_store()
    watcher = await import_cursor_transcripts(since=0, home=tmp_path, store=store, model="auto")
    try:
        record = store.load_records(watcher.session_ids[0])[0]
        assert record["request"]["body"]["model"] == "grok-4.5"
    finally:
        watcher.close()


@pytest.mark.asyncio
async def test_cursor_meta_cache_retries_empty_until_populated(trace_db, tmp_path: Path, monkeypatch) -> None:
    cursor_session = "late-meta-session"
    transcript = _transcript_path(tmp_path, "project-one", cursor_session)
    _write_transcript(
        transcript,
        [
            {"role": "user", "message": {"content": [{"type": "text", "text": "hi"}]}},
            {"role": "assistant", "message": {"content": [{"type": "text", "text": "yo"}]}},
        ],
    )
    from claude_tap.cursor_metadata import CursorConversationMeta
    from claude_tap.trace_store import get_trace_store

    calls = {"n": 0}

    def fake_resolve(conversation_id, *, home=None, state_db=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return CursorConversationMeta()
        if calls["n"] == 2:
            return CursorConversationMeta(context_tokens_used=99, context_token_limit=1000, source="composerData")
        return CursorConversationMeta(model="late-model", context_tokens_used=99, source="chat-store")

    monkeypatch.setattr("claude_tap.cursor_transcript.resolve_cursor_conversation_meta", fake_resolve)
    store = get_trace_store()
    watcher = CursorTranscriptWatcher(since=0, home=tmp_path, store=store)
    assert watcher._meta_for(transcript).model == ""
    assert watcher._meta_for(transcript).context_tokens_used == 99
    assert watcher._meta_for(transcript).model == "late-model"
    assert calls["n"] == 3
    assert watcher._meta_for(transcript).model == "late-model"
    assert calls["n"] == 3
    watcher.close()


@pytest.mark.asyncio
async def test_cursor_transcript_watcher_resets_when_file_shrinks(trace_db, tmp_path: Path) -> None:
    cursor_session = "rewrite-session"
    transcript = _transcript_path(tmp_path, "project-one", cursor_session)
    long_rows = [
        {"role": "user", "message": {"content": [{"type": "text", "text": "old long prompt"}]}},
        {"role": "assistant", "message": {"content": [{"type": "text", "text": "old answer"}]}},
        {"role": "user", "message": {"content": [{"type": "text", "text": "second"}]}},
        {"role": "assistant", "message": {"content": [{"type": "text", "text": "second answer"}]}},
    ]
    _write_transcript(transcript, long_rows)

    from claude_tap.trace_store import get_trace_store

    store = get_trace_store()
    watcher = CursorTranscriptWatcher(since=0, home=tmp_path, store=store)
    assert await watcher.sync_once() == 2
    db_session = watcher.session_ids[0]

    # Cursor rewrote/truncated the same jsonl with a shorter conversation.
    short_rows = [
        {"role": "user", "message": {"content": [{"type": "text", "text": "new"}]}},
        {"role": "assistant", "message": {"content": [{"type": "text", "text": "fresh"}]}},
    ]
    _write_transcript(transcript, short_rows)
    assert await watcher.sync_once() == 1
    watcher.close()

    records = store.load_records(db_session)
    assert any(r["request"]["body"]["messages"][0]["content"] == "new" for r in records)


def test_load_transcript_skips_invalid_and_non_message_rows(tmp_path: Path) -> None:
    path = tmp_path / "broken.jsonl"
    path.write_text(
        "\n".join(
            [
                "not-json",
                json.dumps(["list"]),
                json.dumps({"role": "system", "message": {"content": [{"type": "text", "text": "x"}]}}),
                json.dumps({"role": "user", "message": {"content": []}}),
                json.dumps({"role": "user", "message": {"content": [{"type": "text", "text": "ok"}]}}),
                json.dumps({"role": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}),
            ]
        ),
        encoding="utf-8",
    )
    messages = _load_transcript(path)
    assert messages == [
        ("user", [{"type": "text", "text": "ok"}]),
        ("assistant", [{"type": "text", "text": "hi"}]),
    ]


def test_cursor_project_slug_and_find_transcripts(tmp_path: Path) -> None:
    assert _cursor_project_slug(Path("nope.jsonl")) == ""
    assert _cursor_project_slug(Path("agent-transcripts") / "a" / "a.jsonl") == ""
    transcript = _transcript_path(tmp_path, "proj", "sid")
    _write_transcript(
        transcript,
        [
            {"role": "user", "message": {"content": [{"type": "text", "text": "q"}]}},
            {"role": "assistant", "message": {"content": [{"type": "text", "text": "a"}]}},
        ],
    )
    flat = tmp_path / ".cursor" / "projects" / "proj-flat" / "agent-transcripts" / "flat-sid.jsonl"
    _write_transcript(
        flat,
        [
            {"role": "user", "message": {"content": [{"type": "text", "text": "flat"}]}},
            {"role": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}},
        ],
    )
    assert _cursor_project_slug(transcript) == "proj"
    found = find_cursor_transcripts(since=0, home=tmp_path)
    assert transcript in found
    assert flat in found
    assert find_cursor_transcripts(since=10**12, home=tmp_path) == []
    assert find_cursor_transcripts(since=0, home=tmp_path / "missing-home") == []


def test_build_records_includes_context_meta_and_skips_steps(tmp_path: Path) -> None:
    transcript = _transcript_path(tmp_path, "proj", "sid-meta")
    _write_transcript(
        transcript,
        [
            {"role": "user", "message": {"content": [{"type": "text", "text": "one"}]}},
            {"role": "assistant", "message": {"content": [{"type": "text", "text": "r1"}]}},
            {"role": "user", "message": {"content": [{"type": "text", "text": "two"}]}},
            {"role": "assistant", "message": {"content": [{"type": "text", "text": "r2"}]}},
        ],
    )
    meta = CursorConversationMeta(
        model="from-meta",
        context_tokens_used=11,
        context_token_limit=22,
        source="composerData",
    )
    total, records = build_cursor_transcript_records(
        transcript,
        skip_steps=1,
        conversation_meta=meta,
    )
    assert total == 2
    assert len(records) == 1
    body = records[0]["request"]["body"]
    assert body["model"] == "from-meta"
    assert body["cursor_context_tokens_used"] == 11
    assert body["cursor_context_token_limit"] == 22
    assert body["cursor_meta_source"] == "composerData"
    assert body["messages"][0]["content"] == "two"


@pytest.mark.asyncio
async def test_watcher_get_summary_and_start_stop(trace_db, tmp_path: Path) -> None:
    transcript = _transcript_path(tmp_path, "proj", "summary-session")
    _write_transcript(
        transcript,
        [
            {"role": "user", "message": {"content": [{"type": "text", "text": "ping"}]}},
            {"role": "assistant", "message": {"content": [{"type": "text", "text": "pong"}]}},
        ],
    )
    from claude_tap.trace_store import get_trace_store

    store = get_trace_store()
    watcher = CursorTranscriptWatcher(
        since=0,
        home=tmp_path,
        store=store,
        poll_interval_seconds=0.05,
    )
    await watcher.start()
    await watcher.start()  # idempotent
    await asyncio.sleep(0.12)
    summary = watcher.get_summary()
    assert summary["api_calls"] >= 1
    assert watcher.session_ids
    imported = await watcher.stop()
    assert imported >= 0
    assert isinstance(watcher.get_summary(), dict)


@pytest.mark.asyncio
async def test_async_main_cursor_transcript_only_skips_proxy(monkeypatch, tmp_path: Path, capsys) -> None:
    from unittest.mock import AsyncMock

    from claude_tap import async_main, parse_args

    class FakeWatcher:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.session_ids = ["cursor-session-1"]

        async def start(self) -> None:
            return None

        async def stop(self) -> int:
            return 3

        def close(self) -> None:
            return None

        def get_summary(self) -> JsonObject:
            return {
                "api_calls": 3,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_create_tokens": 0,
                "models_used": {"grok-4.5": 3},
                "has_error": False,
            }

    client_calls: list[JsonObject] = []

    async def fake_run_client(*args, **kwargs):
        client_calls.append(kwargs)
        return 0

    ca_calls: list[object] = []

    monkeypatch.setenv("CLOUDTAP_DB", str(tmp_path / "cursor-async-main.sqlite3"))
    monkeypatch.setattr("claude_tap.cli.run_client", fake_run_client)
    monkeypatch.setattr("claude_tap.cli.CursorTranscriptWatcher", FakeWatcher)
    monkeypatch.setattr("claude_tap.cli.ensure_ca", lambda: ca_calls.append("ca") or (Path("c"), Path("k")))
    monkeypatch.setattr(
        "claude_tap.cli.ensure_shared_dashboard",
        AsyncMock(return_value=("http://127.0.0.1:9/dashboard", False)),
    )

    args = parse_args(
        [
            "--tap-client",
            "cursor",
            "--tap-output-dir",
            str(tmp_path),
            "--tap-no-open",
            "--",
            "--model",
            "grok-4.5",
            "hello",
        ]
    )
    code = await async_main(args)
    assert code == 0
    assert ca_calls == []
    assert client_calls and client_calls[0].get("ca_cert_path") is None
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "watching Cursor agent-transcripts" in captured.err
    assert "Cursor transcript turns: 3" in captured.err


@pytest.mark.asyncio
async def test_async_main_cursor_no_launch_watch_only(monkeypatch, tmp_path: Path, capsys) -> None:
    from claude_tap import async_main, parse_args

    class FakeWatcher:
        last_since: float | None = None

        def __init__(self, **kwargs):
            self.session_ids = []
            FakeWatcher.last_since = kwargs.get("since")

        async def start(self) -> None:
            return None

        async def stop(self) -> int:
            return 0

        def close(self) -> None:
            return None

        def get_summary(self) -> JsonObject:
            return {
                "api_calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_create_tokens": 0,
                "models_used": {},
                "has_error": False,
            }

    sleeps = {"n": 0}
    dashboard_started_at = {"t": None}
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds):
        if seconds >= 3600:
            sleeps["n"] += 1
            raise asyncio.CancelledError
        await real_sleep(seconds)

    async def slow_dashboard(**_kwargs):
        await real_sleep(0.05)
        dashboard_started_at["t"] = __import__("time").time()
        return "http://127.0.0.1:9/dashboard", True

    monkeypatch.setenv("CLOUDTAP_DB", str(tmp_path / "cursor-no-launch.sqlite3"))
    monkeypatch.setattr("claude_tap.cli.CursorTranscriptWatcher", FakeWatcher)
    monkeypatch.setattr("claude_tap.cli.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("claude_tap.cli.ensure_shared_dashboard", slow_dashboard)

    args = parse_args(
        [
            "--tap-client",
            "cursor",
            "--tap-no-launch",
            "--tap-no-open",
            "--tap-output-dir",
            str(tmp_path),
        ]
    )
    code = await async_main(args)
    assert code == 0
    assert sleeps["n"] == 1
    assert FakeWatcher.last_since is not None
    assert dashboard_started_at["t"] is not None
    assert FakeWatcher.last_since <= dashboard_started_at["t"]
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "watching local Cursor transcripts only" in captured.err


@pytest.mark.asyncio
async def test_watcher_skips_user_only_transcript_until_assistant(trace_db, tmp_path: Path) -> None:
    transcript = _transcript_path(tmp_path, "proj", "pending-user")
    _write_transcript(
        transcript,
        [{"role": "user", "message": {"content": [{"type": "text", "text": "waiting"}]}}],
    )
    from claude_tap.trace_store import get_trace_store

    store = get_trace_store()
    watcher = CursorTranscriptWatcher(since=0, home=tmp_path, store=store)
    assert await watcher.sync_once() == 0
    assert watcher.session_ids == []
    _write_transcript(
        transcript,
        [
            {"role": "user", "message": {"content": [{"type": "text", "text": "waiting"}]}},
            {"role": "assistant", "message": {"content": [{"type": "text", "text": "ready"}]}},
        ],
    )
    assert await watcher.sync_once() == 1
    watcher.close()


def _legacy_cursor_record(conversation_id: str) -> JsonObject:
    body: JsonObject = {"messages": [{"role": "user", "content": "inspect"}]}
    return {
        "transport": "cursor-transcript",
        "capture": {"cursor_transcript_id": conversation_id, "client": "cursor"},
        "request": {"method": "CURSOR_TRANSCRIPT", "path": "/cursor/transcript/x/turn/1/step/1", "body": body},
        "response": {
            "status": 200,
            "body": {
                "content": [
                    {"type": "tool_use", "name": "Glob", "input": {"glob_pattern": "**/*.py"}},
                    {
                        "type": "tool_use",
                        "name": "Read",
                        "input": {
                            "path": "/tmp/a.py",
                            "limit": 10,
                            "offset": 1.5,
                            "ok": True,
                            "tags": ["py"],
                            "opts": {"x": 1},
                            "unused": None,
                        },
                    },
                ]
            },
        },
    }


def test_backfill_cursor_transcript_request_fields_writes_tools_and_system(trace_db, tmp_path: Path) -> None:
    conversation_id = "backfill-session"
    store_db = tmp_path / ".cursor" / "chats" / "ws" / conversation_id / "store.db"
    store_db.parent.mkdir(parents=True)
    import sqlite3

    conn = sqlite3.connect(store_db)
    conn.execute("CREATE TABLE blobs (id TEXT PRIMARY KEY, data BLOB)")
    conn.execute(
        "INSERT INTO blobs(id, data) VALUES (?, ?)",
        ("sys", json.dumps({"role": "system", "content": "You are Cursor Grok for backfill."}).encode()),
    )
    conn.commit()
    conn.close()

    from claude_tap.trace_store import get_trace_store

    store = get_trace_store()
    session_id = store.create_session(client="cursor", proxy_mode="transcript")
    store.append_record(session_id, _legacy_cursor_record(conversation_id))
    store.append_record(
        session_id,
        {"transport": "cursor-transcript", "request": {"body": None}, "response": {"body": {}}},
    )
    store.append_record(
        session_id,
        {"transport": "http", "request": {"body": {}}, "response": {"body": {}}},
    )
    other_id = store.create_session(client="claude", proxy_mode="reverse")
    store.append_record(
        other_id,
        {"request": {"body": {"messages": [{"role": "user", "content": "hi"}]}}, "response": {"body": {}}},
    )

    updated = backfill_cursor_transcript_request_fields(store, home=tmp_path)
    assert updated == 1
    record = store.load_records(session_id)[0]
    tools = record["request"]["body"]["tools"]
    assert [tool["name"] for tool in tools] == ["Glob", "Read"]
    assert tools[0]["input_schema"]["properties"]["glob_pattern"] == {"type": "string"}
    assert tools[1]["input_schema"]["properties"]["limit"] == {"type": "integer"}
    assert tools[1]["input_schema"]["properties"]["offset"] == {"type": "number"}
    assert tools[1]["input_schema"]["properties"]["ok"] == {"type": "boolean"}
    assert tools[1]["input_schema"]["properties"]["tags"] == {"type": "array"}
    assert tools[1]["input_schema"]["properties"]["opts"] == {"type": "object"}
    assert record["request"]["body"]["system"] == "You are Cursor Grok for backfill."
    assert "tools" not in store.load_records(other_id)[0]["request"]["body"]
    assert backfill_cursor_transcript_request_fields(store, home=tmp_path) == 0


@pytest.mark.asyncio
async def test_cursor_watcher_backfills_existing_records_on_first_sync(trace_db, tmp_path: Path) -> None:
    conversation_id = "watcher-backfill"
    from claude_tap.trace_store import get_trace_store

    store = get_trace_store()
    session_id = store.create_session(client="cursor", proxy_mode="transcript")
    store.append_record(session_id, _legacy_cursor_record(conversation_id))
    snapshot = store.dashboard_snapshot()

    watcher = CursorTranscriptWatcher(since=0, home=tmp_path, store=store)
    try:
        assert await watcher.sync_once() == 0
        record = store.load_records(session_id)[0]
        assert record["request"]["body"]["tools"][0]["name"] == "Glob"
        assert store.dashboard_snapshot() == snapshot
        assert await watcher.sync_once() == 0
    finally:
        watcher.close()


@pytest.mark.asyncio
async def test_cursor_transcript_imports_system_prompt_from_chat_store(trace_db, tmp_path: Path) -> None:
    cursor_session = "system-session"
    transcript = _transcript_path(tmp_path, "project-one", cursor_session)
    _write_transcript(
        transcript,
        [
            {"role": "user", "message": {"content": [{"type": "text", "text": "hi"}]}},
            {"role": "assistant", "message": {"content": [{"type": "text", "text": "yo"}]}},
        ],
    )
    store_db = tmp_path / ".cursor" / "chats" / "ws" / cursor_session / "store.db"
    store_db.parent.mkdir(parents=True)
    import sqlite3

    conn = sqlite3.connect(store_db)
    conn.execute("CREATE TABLE meta (key TEXT, value TEXT)")
    conn.execute("CREATE TABLE blobs (id TEXT PRIMARY KEY, data BLOB)")
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?)",
        ("0", json.dumps({"lastUsedModel": "grok-4.6"}).encode().hex()),
    )
    conn.execute(
        "INSERT INTO blobs(id, data) VALUES (?, ?)",
        ("sys", json.dumps({"role": "system", "content": "Imported Cursor system prompt."}).encode()),
    )
    conn.commit()
    conn.close()

    from claude_tap.trace_store import get_trace_store

    store = get_trace_store()
    watcher = await import_cursor_transcripts(since=0, home=tmp_path, store=store)
    try:
        record = store.load_records(watcher.session_ids[0])[0]
        assert record["request"]["body"]["system"] == "Imported Cursor system prompt."
        assert record["request"]["body"]["model"] == "grok-4.6"
    finally:
        watcher.close()
