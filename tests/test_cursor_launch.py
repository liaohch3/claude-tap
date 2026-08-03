from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from claude_tap import parse_args
from claude_tap.cli import CLIENT_CONFIGS, run_client
from claude_tap.cursor_transcript import (
    CursorTranscriptWatcher,
    import_cursor_transcripts,
    model_from_cursor_args,
)


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


def _write_transcript(path: Path, rows: list[dict]) -> None:
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
    assert model_from_cursor_args(["--model=auto"]) == "auto"


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
    captured: dict[str, object] = {}
    ca_path = Path("/tmp/test-ca.pem")

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _DummyProc()

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
