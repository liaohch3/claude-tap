from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest

from claude_tap.hermes_session import HermesSessionResolver, hermes_request_timestamp
from claude_tap.trace import create_trace_writer
from claude_tap.trace_log_handler import SQLiteLogHandler
from claude_tap.trace_store import TraceStore


def _seed_hermes_state(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            parent_session_id TEXT,
            source TEXT,
            started_at REAL,
            ended_at REAL,
            message_count INTEGER
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT,
            timestamp REAL NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        );
        """
    )
    conn.executemany(
        "INSERT INTO sessions (id, parent_session_id, source, started_at, ended_at, message_count) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("root-a", None, "cli", 1.0, None, 2),
            ("child-a", "root-a", "subagent", 2.0, None, 2),
            ("root-b", None, "cli", 3.0, None, 2),
        ],
    )
    conn.executemany(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, 'user', ?, ?)",
        [
            ("root-a", "parent prompt", 1.0),
            ("child-a", "child prompt", 2.0),
            ("root-b", "new conversation", 3.0),
        ],
    )
    conn.commit()
    conn.close()


def _seed_parallel_hermes_state(path: Path) -> None:
    """Seed a root plus two overlapping subagent sessions.

    Both children receive the same first user prompt, which is the real-world
    ambiguity this resolver must surface instead of assigning both to one
    arbitrary leaf.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            parent_session_id TEXT,
            source TEXT,
            started_at REAL NOT NULL,
            ended_at REAL,
            message_count INTEGER NOT NULL
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT,
            timestamp REAL NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        );
        """
    )
    conn.executemany(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("root", None, "cli", 100.0, None, 4),
            ("child-a", "root", "subagent", 200.0, 220.0, 4),
            ("child-b", "root", "subagent", 201.0, 230.0, 4),
        ],
    )
    conn.executemany(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, 'user', ?, ?)",
        [
            ("root", "parent prompt", 100.0),
            ("child-a", "parallel prompt", 210.0),
            ("child-b", "parallel prompt", 213.0),
            ("root", "second root prompt", 220.5),
        ],
    )
    conn.commit()
    conn.close()


def _record(turn: int, prompt: str, *, message_count: int = 2) -> dict:
    messages = [{"role": "system", "content": "You are Hermes."}, {"role": "user", "content": prompt}]
    while len(messages) < message_count:
        messages.append({"role": "assistant", "content": "continuation"})
    return {
        "turn": turn,
        "timestamp": f"2026-08-10T00:00:{turn:02d}+00:00",
        "request": {
            "method": "POST",
            "path": "/v1/chat/completions",
            "body": {"model": "test-model", "messages": messages},
        },
        "response": {
            "status": 200,
            "body": {"usage": {"prompt_tokens": 10, "completion_tokens": 2}},
        },
    }


@pytest.mark.asyncio
async def test_hermes_root_conversations_rotate_tap_sessions_but_children_stay_with_parent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    hermes_home = tmp_path / "hermes"
    _seed_hermes_state(hermes_home / "profiles" / "test" / "state.db")
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    store = TraceStore(tmp_path / "traces.sqlite3")
    writer = create_trace_writer(
        store=store,
        client="hermes",
        proxy_mode="reverse",
        metadata={"client": "hermes", "proxy_mode": "reverse"},
    )

    await writer.write(_record(1, "parent prompt"))
    await writer.write(_record(2, "child prompt"))
    await writer.write(_record(3, "new conversation"))

    handler = SQLiteLogHandler(writer.session_ids[0], store=store, session_id_getter=lambda: writer.session_id)
    handler.emit(logging.LogRecord("test", logging.INFO, __file__, 1, "after rotation", (), None))

    assert len(writer.session_ids) == 2
    first_records = store.load_records(writer.session_ids[0])
    second_records = store.load_records(writer.session_ids[1])
    assert [record["capture"]["hermes_session_id"] for record in first_records] == ["root-a", "root-a"]
    assert [record["capture"]["hermes_session_id"] for record in second_records] == ["root-b"]
    assert {
        "hermes_session_id",
        "hermes_root_session_id",
        "hermes_leaf_session_id",
        "hermes_parent_session_id",
        "hermes_session_source",
        "hermes_session_resolution",
    }.issubset(first_records[0]["capture"])
    assert first_records[0]["capture"]["hermes_root_session_id"] == "root-a"
    assert first_records[0]["capture"]["hermes_leaf_session_id"] == "root-a"
    assert first_records[0]["capture"]["hermes_parent_session_id"] is None
    assert first_records[0]["capture"]["hermes_session_source"] == "cli"
    assert first_records[0]["capture"]["hermes_root_turn"] == 1
    assert first_records[1]["capture"]["hermes_leaf_session_id"] == "child-a"
    assert first_records[1]["capture"]["hermes_parent_session_id"] == "root-a"
    assert first_records[1]["capture"]["hermes_session_source"] == "subagent"
    assert first_records[1]["capture"]["hermes_root_turn"] == 1
    assert second_records[0]["capture"]["hermes_leaf_session_id"] == "root-b"
    assert [record["turn"] for record in first_records] == [1, 2]
    assert [record["turn"] for record in second_records] == [1]
    assert store.load_session_row(writer.session_ids[0])["status"] == "complete"
    assert store.load_logs(writer.session_ids[0]) == []
    assert [entry["message"] for entry in store.load_logs(writer.session_ids[1])] == ["after rotation"]

    writer.close()
    assert store.load_session_row(writer.session_ids[1])["status"] == "complete"


@pytest.mark.asyncio
async def test_hermes_continuation_does_not_query_as_new_conversation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    hermes_home = tmp_path / "hermes"
    _seed_hermes_state(hermes_home / "state.db")
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    store = TraceStore(tmp_path / "traces.sqlite3")
    writer = create_trace_writer(
        store=store,
        client="hermes",
        proxy_mode="reverse",
        metadata={"client": "hermes", "proxy_mode": "reverse"},
    )

    await writer.write(_record(1, "parent prompt"))
    await writer.write(_record(2, "parent prompt", message_count=8))

    assert writer.session_ids == [writer.session_id]
    assert len(store.load_records(writer.session_id)) == 2


@pytest.mark.asyncio
async def test_hermes_proxy_restart_reuses_existing_tap_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    hermes_home = tmp_path / "hermes"
    _seed_hermes_state(hermes_home / "state.db")
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    store = TraceStore(tmp_path / "traces.sqlite3")
    metadata = {"client": "hermes", "proxy_mode": "reverse"}

    first_writer = create_trace_writer(
        store=store,
        client="hermes",
        proxy_mode="reverse",
        metadata=metadata,
    )
    await first_writer.write_next_turn(_record(99, "parent prompt"))
    canonical_session_id = first_writer.session_id
    first_writer.close()

    restarted_writer = create_trace_writer(
        store=store,
        client="hermes",
        proxy_mode="reverse",
        metadata=metadata,
    )
    startup_shell_id = restarted_writer.session_id
    assert startup_shell_id != canonical_session_id

    await restarted_writer.write_next_turn(_record(99, "parent prompt", message_count=8))

    assert restarted_writer.session_id == canonical_session_id
    assert restarted_writer.session_ids == [canonical_session_id]
    assert store.load_session_row(startup_shell_id) is None
    records = store.load_records(canonical_session_id)
    assert [record["turn"] for record in records] == [1, 2]
    assert [record["capture"]["hermes_session_id"] for record in records] == ["root-a", "root-a"]

    restarted_writer.close()
    row = store.load_session_row(canonical_session_id)
    assert row is not None
    assert row["record_count"] == 2
    assert row["status"] == "complete"


@pytest.mark.asyncio
async def test_hermes_restart_moves_startup_probes_into_existing_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    hermes_home = tmp_path / "hermes"
    _seed_hermes_state(hermes_home / "state.db")
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    store = TraceStore(tmp_path / "traces.sqlite3")
    metadata = {"client": "hermes", "proxy_mode": "reverse"}

    first_writer = create_trace_writer(
        store=store,
        client="hermes",
        proxy_mode="reverse",
        metadata=metadata,
    )
    await first_writer.write_next_turn(_record(99, "parent prompt"))
    canonical_session_id = first_writer.session_id
    first_writer.close()

    restarted_writer = create_trace_writer(
        store=store,
        client="hermes",
        proxy_mode="reverse",
        metadata=metadata,
    )
    startup_shell_id = restarted_writer.session_id
    startup_probe = _record(99, "")
    startup_probe["request"]["path"] = "/api/v1/models"
    startup_probe["request"]["body"] = None
    startup_probe["response"] = {"status": 404, "body": {"error": "probe failed"}}
    await restarted_writer.write_next_turn(startup_probe)
    await restarted_writer.write_next_turn(_record(99, "parent prompt", message_count=8))

    assert restarted_writer.session_id == canonical_session_id
    assert store.load_session_row(startup_shell_id) is None
    records = store.load_records(canonical_session_id)
    assert [record["turn"] for record in records] == [1, 2, 3]
    assert [record["request"]["path"] for record in records] == [
        "/v1/chat/completions",
        "/api/v1/models",
        "/v1/chat/completions",
    ]
    assert records[1]["capture"]["source_session_id"] == startup_shell_id
    assert records[2]["capture"]["hermes_session_id"] == "root-a"


def test_hermes_parallel_children_are_explicitly_ambiguous(tmp_path: Path) -> None:
    state_db = tmp_path / "state.db"
    _seed_parallel_hermes_state(state_db)
    resolver = HermesSessionResolver(tmp_path)

    ambiguous = resolver.resolve_session("parallel prompt", request_timestamp=211.5, message_count=4)
    assert ambiguous.root_session_id == "root"
    assert ambiguous.leaf_session_id is None
    assert ambiguous.parent_session_id is None
    assert ambiguous.source is None
    assert ambiguous.root_turn == 1
    assert ambiguous.resolution == "ambiguous"

    unique = resolver.resolve_session("parallel prompt", request_timestamp=228.0, message_count=4)
    assert unique.root_session_id == "root"
    assert unique.leaf_session_id == "child-b"
    assert unique.parent_session_id == "root"
    assert unique.source == "subagent"
    assert unique.root_turn == 2
    assert unique.resolution == "exact"

    second_root = resolver.resolve_session("second root prompt", request_timestamp=228.0, message_count=4)
    assert second_root.leaf_session_id == "root"
    assert second_root.root_turn == 2


def test_hermes_request_start_disambiguates_parallel_children(tmp_path: Path) -> None:
    state_db = tmp_path / "state.db"
    _seed_parallel_hermes_state(state_db)
    resolver = HermesSessionResolver(tmp_path)

    def request(completion: int, duration_ms: int) -> dict:
        return {"timestamp": str(completion), "duration_ms": duration_ms}

    first = request(1210, 1_000_000)
    second = request(1213, 1_000_000)
    assert hermes_request_timestamp(first) == 210.0
    assert hermes_request_timestamp(second) == 213.0
    assert (
        resolver.resolve_session(
            "parallel prompt", request_timestamp=hermes_request_timestamp(first), message_count=4
        ).leaf_session_id
        == "child-a"
    )
    assert (
        resolver.resolve_session(
            "parallel prompt", request_timestamp=hermes_request_timestamp(second), message_count=4
        ).leaf_session_id
        == "child-b"
    )


def test_hermes_request_time_beats_stale_final_message_count(tmp_path: Path) -> None:
    state_db = tmp_path / "state.db"
    conn = sqlite3.connect(state_db)
    conn.executescript(
        """
        CREATE TABLE sessions (id TEXT PRIMARY KEY, parent_session_id TEXT, source TEXT,
                               started_at REAL, ended_at REAL, message_count INTEGER);
        CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT,
                               role TEXT, content TEXT, timestamp REAL, active INTEGER DEFAULT 1);
        """
    )
    conn.executemany(
        "INSERT INTO sessions VALUES (?, NULL, 'cli', ?, ?, ?)",
        [("old", 100.0, 150.0, 2), ("current", 200.0, None, 3)],
    )
    conn.executemany(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, 'user', 'same', ?)",
        [("old", 100.0), ("current", 200.0)],
    )
    conn.commit()
    conn.close()

    match = HermesSessionResolver(tmp_path).resolve_session("same", request_timestamp=202.0, message_count=2)
    assert match.leaf_session_id == "current"
