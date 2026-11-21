from __future__ import annotations

import json
import sqlite3
from contextlib import closing

import pytest

from claude_tap import manus_cache
from claude_tap.manus_cache import read_terminal_events

TASK_ID = "testtask0123456789abcd"
OTHER_TASK_ID = "testtask0123456789abce"
EARLY_TIMESTAMP_MS = 1_795_227_000_123
LATE_TIMESTAMP_MS = 1_795_227_016_147


@pytest.fixture
def cache_path(tmp_path):
    path = tmp_path / "Manus #1% cache.sqlite"
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript("""
            CREATE TABLE session_view_event (
                key TEXT PRIMARY KEY NOT NULL, scope TEXT NOT NULL, account TEXT NOT NULL,
                id TEXT NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL, bytes INTEGER NOT NULL
            );
            CREATE INDEX session_view_event_scope ON session_view_event(scope, key);
        """)
    return path


def _terminal(event_id, timestamp, command, output, *, finished=True):
    return {
        "id": event_id,
        "type": "toolUsed",
        "tool": "terminal",
        "timestamp": timestamp,
        "detail": {"terminal": {"command": command, "output": output, "finished": finished}},
    }


def _store(path, event, *, task_id=TASK_ID, owner="test-owner", row_id=None, version=1, payload=None):
    row_id = row_id or event["id"]
    account = json.dumps([owner, "prod"], separators=(",", ":"))
    scope = json.dumps([owner, "prod", task_id], separators=(",", ":"))
    key = json.dumps([scope, row_id], separators=(",", ":"))
    raw = json.dumps(event, ensure_ascii=False) if payload is None else payload
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "INSERT OR REPLACE INTO session_view_event VALUES (?,?,?,?,?,?,?)",
            (key, scope, account, row_id, version, raw, len(raw.encode("utf-8"))),
        )


def test_exact_task_projection_preserves_order_output_and_database_contents(cache_path):
    latest = _terminal("aaa", LATE_TIMESTAMP_MS, "printf later", ["later"])
    earlier = _terminal("zzz", EARLY_TIMESTAMP_MS, "printf hello", ["你好", "second line"])
    earlier["detail"]["terminal"]["internalField"] = "unrelated detail"
    _store(cache_path, latest)
    _store(cache_path, earlier)
    _store(cache_path, _terminal("other", 15, "printf different task", ["different output"]), task_id=OTHER_TASK_ID)
    _store(cache_path, _terminal("preview", 14, "printf awaiting approval", [], finished=False))
    _store(cache_path, {**_terminal("network", 15, "nonterminal", ["not shown"]), "tool": "browser"})
    _store(cache_path, {"id": "chat", "type": "chat", "content": "unrelated chat"})
    before = cache_path.read_bytes()

    assert read_terminal_events(TASK_ID, cache_path) == [
        {
            "id": "zzz",
            "type": "tool_call",
            "tool_name": "terminal",
            "command": "printf hello",
            "output": "你好\nsecond line",
            "timestamp_ms": EARLY_TIMESTAMP_MS,
        },
        {
            "id": "aaa",
            "type": "tool_call",
            "tool_name": "terminal",
            "command": "printf later",
            "output": "later",
            "timestamp_ms": LATE_TIMESTAMP_MS,
        },
    ]
    assert cache_path.read_bytes() == before


def test_removes_ansi_display_sequences_without_changing_terminal_text(cache_path):
    command = r"printf '\033[32mtest'"
    output = [
        "  \x1b[32m你好\x1b[0m [32m \t",
        "\x1b]0;Window title\x07\x1b]8;;https://example.invalid\x1b\\Link\x1b]8;;\x1b\\",
        "\x9b?25lc1\x9b0m\r",
    ]
    _store(cache_path, _terminal("color", EARLY_TIMESTAMP_MS, command, output))

    event = read_terminal_events(TASK_ID, cache_path)[0]

    assert event["output"] == "  你好 [32m \t\nLink\nc1\r"
    assert event["command"] == command


def test_updates_use_latest_cache_version_with_same_event_id(cache_path):
    _store(cache_path, _terminal("stable", 5, "printf updated", []))
    initial = read_terminal_events(TASK_ID, cache_path)
    _store(cache_path, _terminal("stable", 5, "printf updated", ["final"]), version=2)

    assert len(initial) == 1
    assert initial[0]["output"] == ""
    assert read_terminal_events(TASK_ID, cache_path) == [{**initial[0], "output": "final"}]


def test_same_task_in_two_account_scopes_is_ambiguous(cache_path):
    _store(cache_path, _terminal("terminal", 5, "printf test", ["test"]))
    _store(cache_path, {"id": "chat", "type": "chat"}, owner="another-test-owner")

    assert read_terminal_events(TASK_ID, cache_path) == []


@pytest.mark.parametrize(
    "override",
    [
        {"timestamp": "5"},
        {"timestamp": True},
        {"timestamp": 253_402_300_800_000},
        {"detail": {"terminal": {"command": "echo test", "output": [42], "finished": True}}},
        {"detail": {"terminal": {"command": "", "output": ["text"], "finished": True}}},
        {"detail": {"terminal": {"command": "echo test", "output": "text", "finished": True}}},
        {"detail": {"terminal": {"command": "echo test", "output": ["text"], "finished": "true"}}},
        {"detail": []},
    ],
)
def test_unrecognized_terminal_rows_are_excluded(cache_path, override):
    _store(cache_path, {**_terminal("bad", 5, "printf test", ["raw"]), **override})
    _store(cache_path, _terminal("wrong-id", 5, "printf test", ["raw"]), row_id="different")
    _store(cache_path, {"id": "broken-json"}, payload="{not JSON")

    assert read_terminal_events(TASK_ID, cache_path) == []


def test_inaccessible_or_invalid_database_returns_empty_without_creating_file(tmp_path):
    absent = tmp_path / "missing.sqlite"
    corrupt = tmp_path / "corrupt.sqlite"
    corrupt.write_bytes(b"invalid SQLite data")

    assert read_terminal_events(TASK_ID, absent) == []
    assert not absent.exists()
    assert read_terminal_events(TASK_ID, corrupt) == []


@pytest.mark.parametrize("task_id", [None, "", "short", f"{TASK_ID}/"])
def test_invalid_task_id_returns_empty(cache_path, task_id):
    _store(cache_path, _terminal("command", 5, "printf test", ["test"]))

    assert read_terminal_events(task_id, cache_path) == []


def test_bounded_reader_excludes_oversized_rows_and_rejects_incomplete_batches(cache_path, monkeypatch):
    _store(cache_path, _terminal("big", 3, "printf ignored", ["a" * 1_000]))
    _store(cache_path, _terminal("one", 4, "printf test", ["one"]))
    _store(cache_path, _terminal("two", 5, "printf test", ["two"]))
    monkeypatch.setattr(manus_cache, "_MAX_EVENT_BYTES", 300)

    assert [event["id"] for event in read_terminal_events(TASK_ID, cache_path)] == ["one", "two"]
    monkeypatch.setattr(manus_cache, "_MAX_EVENTS", 1)
    assert read_terminal_events(TASK_ID, cache_path) == []
