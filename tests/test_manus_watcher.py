"""Contracts for observing accessible chat messages in Manus Studio."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from claude_tap.manus_watcher import ManusWatcher
from claude_tap.trace_store import TraceStore

_TASK_ID = "0123456789abcdefghijkl"
_OTHER_TASK_ID = "9876543210zyxwvutsrqpo"


def _group(*children, classes=()):
    return {"role": "AXGroup", "dom_classes": list(classes), "children": list(children)}


def _text(value):
    return {"role": "AXStaticText", "value": value}


def _user(value):
    return _group(
        _group(_text(value), classes=("chat-message-body",)),
        {"role": "AXButton", "title": "Copy"},
        classes=("user-message-bubble-new",),
    )


def _assistant(value):
    return _group(_text(value), classes=("whitespace-pre-wrap", "u-break-words"))


def _snapshot(*messages, task_id=_TASK_ID, **metadata):
    return {
        "status": "ok",
        "task_id": task_id,
        "title": "Claude Tap demo",
        "conversation": _group(_group(*messages)),
        **metadata,
    }


def _records_for_task(store, task_id=_TASK_ID):
    for row in store.list_session_rows():
        records = store.load_records(row["id"])
        if records[0]["capture"]["manus_task_id"] == task_id:
            return records
    raise AssertionError(f"No session for {task_id}")


def _event(record):
    return record["response"]["body"]["manus_event"]


def _terminal(event_id, command, output):
    return {"id": event_id, "type": "tool_call", "tool_name": "terminal", "command": command, "output": output}


def test_imports_terminal_events_without_faking_model_metadata_or_overwriting_assistant_preview(tmp_path):
    store = TraceStore(tmp_path / "traces.db")
    watcher = ManusWatcher(store)
    events = [
        {**_terminal("tool1", "printf 'first'", "first"), "timestamp_ms": 1795221000123},
        _terminal("tool2", "printf 'second'", "https://example.test/file?signature=PRIVATE_TEST_VALUE"),
        _terminal("missing-output", "printf 'skip'", None),
        {**_terminal("other-tool", "ignored", "ignored"), "tool_name": "browser"},
        "malformed-event",
    ]
    snapshot = _snapshot(_user("Check the output"), _assistant("Done"), task_status="completed", terminal_events=events)
    assert watcher.sync_snapshot(snapshot) == 4
    assert watcher.sync_snapshot(snapshot) == 0
    user, reply, first, second = _records_for_task(store)
    assert [_event(record)["type"] for record in (user, reply, first, second)] == [
        "user_message",
        "assistant_message",
        "tool_used",
        "tool_used",
    ]
    assert _event(first) == {
        "id": "tool1",
        "type": "tool_used",
        "tool": "terminal",
        "input": {"command": "printf 'first'"},
        "output": "first",
        "timestamp_ms": 1795221000123,
    }
    summary = json.loads(store.list_session_rows()[0]["summary_json"])
    assert summary["last_response"] == "Done"
    assert summary["manus_task_status"] == "completed"
    assert first["capture"]["manus_source"] == "desktop-task-cache"
    assert first["capture"]["timestamp_kind"] == "observed"
    assert "usage" not in first["response"]["body"]
    assert "status" not in first["response"]
    assert "duration_ms" not in first
    assert _event(second)["output"] == "https://example.test/file"
    assert "timestamp_ms" not in _event(second)
    assert [record["turn"] for record in (user, reply, first, second)] == [1, 2, 3, 4]


def test_masks_recognizable_terminal_header_and_shell_secrets_before_storage(tmp_path):
    store = TraceStore(tmp_path / "traces.db")
    watcher = ManusWatcher(store)
    command = (
        'OPENAI_API_KEY="secret alpha" MAX_TOKENS=40 curl '
        "-H 'Authorization: Bearer secret-beta' -H 'X-API-Key: secret-gamma' "
        '-H "Authorization: Bearer ${ACCESS_TOKEN}" -H "Api-Key: $(read_credential)" https://example.test'
    )
    output = (
        "Proxy-Authorization: Basic secret-delta\n"
        "GITHUB_TOKEN=secret-epsilon\n"
        "DATABASE_PASSWORD='secret zeta'\n"
        "OPENAI_API_KEY=$OPENAI_API_KEY\n"
        "TOKEN=$(read_credential)\n"
        "OPENAI_API_KEY= env\n"
        "MAX_TOKENS=40\n"
        "The key number remains meaningful."
    )
    assert watcher.sync_snapshot(_snapshot(terminal_events=[_terminal("tool", command, output)])) == 1
    [record] = _records_for_task(store)
    event = _event(record)
    assert event["input"]["command"] == (
        'OPENAI_API_KEY="REDACTED" MAX_TOKENS=40 curl '
        "-H 'Authorization: Bearer REDACTED' -H 'X-API-Key: REDACTED' "
        '-H "Authorization: Bearer ${ACCESS_TOKEN}" -H "Api-Key: $(read_credential)" https://example.test'
    )
    assert event["output"] == (
        "Proxy-Authorization: Basic REDACTED\n"
        "GITHUB_TOKEN=REDACTED\n"
        "DATABASE_PASSWORD='REDACTED'\n"
        "OPENAI_API_KEY=$OPENAI_API_KEY\n"
        "TOKEN=$(read_credential)\n"
        "OPENAI_API_KEY= env\n"
        "MAX_TOKENS=40\n"
        "The key number remains meaningful."
    )
    assert "secret" not in json.dumps(record)


def test_revises_tool_output_by_source_id_without_breaking_chat_overlap_or_separate_tasks(tmp_path):
    store = TraceStore(tmp_path / "traces.db")
    first = ManusWatcher(store)
    initial = _terminal("same-id", "printf 'output'", "partial")
    assert first.sync_snapshot(_snapshot(_user("Run it"), _assistant("Very"), terminal_events=[initial])) == 3
    old_tool = _records_for_task(store)[-1]

    second = ManusWatcher(TraceStore(store.db_path))
    revised = {**initial, "output": "full output"}
    late = _terminal("late-id", "printf 'late'", "late")
    assert (
        second.sync_snapshot(_snapshot(_user("Run it"), _assistant("Very good"), terminal_events=[revised, late])) == 1
    )
    assert (
        second.sync_snapshot(_snapshot(_user("Run it"), _assistant("Very good"), terminal_events=[revised, late])) == 0
    )
    user, reply, tool, late_tool = _records_for_task(store)
    assert _event(reply)["content"] == "Very good"
    assert tool["request_id"] == old_tool["request_id"]
    assert tool["turn"] == old_tool["turn"]
    assert _event(tool)["output"] == "full output"
    assert _event(late_tool)["id"] == "late-id"

    assert second.sync_snapshot(_snapshot(_user("Follow-up"), terminal_events=[late])) == 0
    assert second.sync_snapshot(_snapshot(_user("Run it"), _assistant("Very good"), _user("Follow-up"))) == 1
    assert second.sync_snapshot(_snapshot(task_id=_OTHER_TASK_ID, terminal_events=[initial])) == 1
    assert _event(_records_for_task(store, _OTHER_TASK_ID)[0])["output"] == "partial"
    assert _event(_records_for_task(store)[2])["output"] == "full output"


def test_imports_attributed_messages_without_copying_other_controls_or_credentials(tmp_path):
    store = TraceStore(tmp_path / "traces.db")
    watcher = ManusWatcher(store)
    snapshot = _snapshot(
        {"role": "AXImage", "description": "Avatar"},
        _user("Inspect a demo file"),
        _group(_text("View the plan"), classes=("tooltip",)),
        _assistant("The [file](https://example.com/demo?X-Amz-Signature=DO_NOT_STORE) is ready."),
        {"role": "AXTextField", "value": "Unsent draft"},
        _group(_text("Manus credits panel"), classes=("whitespace-pre-wrap",)),
    )
    assert watcher.sync_snapshot(snapshot) == 2
    assert watcher.sync_snapshot(snapshot) == 0
    first, second = _records_for_task(store)
    assert first["capture"]["proxy_mode"] == "manus-desktop"
    assert first["capture"]["timestamp_kind"] == "observed"
    assert first["request"]["path"] == f"/manus/tasks/{_TASK_ID}"
    assert first["request"]["body"]["messages"] == [{"role": "user", "content": "Inspect a demo file"}]
    assert _event(first)["type"] == "user_message"
    assert _event(second)["type"] == "assistant_message"
    assert second["response"]["body"]["content"] == [
        {"type": "text", "text": "The [file](https://example.com/demo) is ready."},
    ]
    assert "usage" not in second["response"]["body"]
    assert "duration_ms" not in second
    assert "status" not in second["response"]
    payload = json.dumps((first, second))
    for hidden in ("Avatar", "View the plan", "Unsent draft", "Manus credits panel", "DO_NOT_STORE"):
        assert hidden not in payload


def test_resumes_after_restart_and_app_switch_without_deduplicating_repeated_turns(tmp_path):
    store = TraceStore(tmp_path / "traces.db")
    first = ManusWatcher(store)
    initial = _snapshot(_user("Ping"), _assistant("Pong"))
    assert first.sync_snapshot(initial) == 2
    assert first.sync_snapshot(_snapshot(_user("Other task"), task_id=_OTHER_TASK_ID)) == 1
    assert len(first.session_ids) == store.count_session_rows() == 2
    first.close()
    assert {row["status"] for row in store.list_session_rows()} == {"complete"}

    reopened = TraceStore(store.db_path)
    second = ManusWatcher(reopened)
    assert second.sync_snapshot(initial) == 0
    assert second.sync_snapshot(_snapshot(_user("Ping"), _assistant("Pong"), _user("Ping"), _assistant("Pong"))) == 2
    assert [_event(record)["content"] for record in _records_for_task(reopened)] == ["Ping", "Pong", "Ping", "Pong"]


def test_only_appends_scroll_windows_that_overlap_the_saved_tail(tmp_path):
    store = TraceStore(tmp_path / "traces.db")
    watcher = ManusWatcher(store)
    assert watcher.sync_snapshot(_snapshot(_user("Start"), _assistant("First"), _user("Next"))) == 3
    assert watcher.sync_snapshot(_snapshot(_assistant("Unrelated visible text"))) == 0
    assert watcher.sync_snapshot(_snapshot(_user("Start"), _assistant("First"))) == 0
    assert watcher.sync_snapshot(_snapshot(_assistant("First"), _user("Next"), _assistant("Second"))) == 1
    assert [_event(record)["content"] for record in _records_for_task(store)] == ["Start", "First", "Next", "Second"]


def test_running_followup_anchors_new_virtualized_turn_but_idle_slice_does_not(tmp_path):
    store = TraceStore(tmp_path / "traces.db")
    watcher = ManusWatcher(store)
    previous = _snapshot(_user("Start"), _assistant("First"), is_running=False)
    active_followup = _snapshot(_user("Next"), is_running=True)
    assert watcher.sync_snapshot(previous) == 2
    assert watcher.sync_snapshot(_snapshot(_user("Unanchored old prompt"), _assistant("Earlier"))) == 0
    assert watcher.sync_snapshot(active_followup) == 0
    assert watcher.sync_snapshot(previous) == 0
    assert watcher.sync_snapshot({"status": "no_task"}) == 0
    assert watcher.sync_snapshot(active_followup) == 0
    assert watcher.sync_snapshot(previous) == 0
    assert watcher.sync_snapshot(active_followup) == 1
    assert watcher.sync_snapshot(active_followup) == 0
    assert watcher.sync_snapshot(_snapshot(_user("Next"), _assistant("Second"), is_running=False)) == 1
    assert [_event(record)["content"] for record in _records_for_task(store)] == ["Start", "First", "Next", "Second"]


def test_streaming_reply_waits_when_indicated_and_updates_growing_text_without_duplicate(tmp_path):
    store = TraceStore(tmp_path / "traces.db")
    watcher = ManusWatcher(store)
    assert watcher.sync_snapshot(_snapshot(_user("Explain"), _assistant("Very"), is_running=True)) == 1
    assert len(_records_for_task(store)) == 1
    assert watcher.sync_snapshot(_snapshot(_user("Explain"), _assistant("Very"), is_running=False)) == 1
    previous = _records_for_task(store)[-1]
    assert watcher.sync_snapshot(_snapshot(_user("Explain"), _assistant("Very well."))) == 0
    current = _records_for_task(store)[-1]
    assert current["request_id"] == previous["request_id"]
    assert _event(current)["content"] == "Very well."
    assert watcher.sync_snapshot(_snapshot(_user("Explain"), _assistant("Very well."), _user("More?"))) == 1
    assert [record["turn"] for record in _records_for_task(store)] == [1, 2, 3]


def test_records_observed_task_status_without_inventing_completion(tmp_path):
    store = TraceStore(tmp_path / "traces.db")
    watcher = ManusWatcher(store)
    assert watcher.sync_snapshot(_snapshot(_user("Send"))) == 1
    assert watcher.sync_snapshot(_snapshot(_user("Send"), is_running=True, task_status="thinking")) == 0
    rows = _records_for_task(store)
    assert rows[0]["response"]["body"]["manus_task"]["status"] == "thinking"
    assert watcher.sync_snapshot(_snapshot(_user("Send"), _assistant("Partial"))) == 0
    assert len(_records_for_task(store)) == 1
    assert watcher.sync_snapshot(_snapshot(_user("Send"), _assistant("Done"), is_running=False)) == 1
    assert "status" not in _records_for_task(store)[1]["response"]["body"]["manus_task"]
    assert watcher.sync_snapshot(_snapshot(_user("Send"), _assistant("Done"), task_status="completed")) == 0
    rows = _records_for_task(store)
    assert rows[1]["response"]["body"]["manus_task"]["status"] == "completed"
    assert len(rows) == 2


def test_disappearing_interim_prose_never_blocks_final_reply(tmp_path):
    store = TraceStore(tmp_path / "traces.db")
    watcher = ManusWatcher(store)
    previous = (_user("Start"), _assistant("First"))
    followup = _user("Use the terminal")
    narration = _assistant("I'll open the terminal.")
    assert watcher.sync_snapshot(_snapshot(*previous)) == 2
    assert watcher.sync_snapshot({"status": "snapshot_incomplete"}) == 0
    assert watcher.sync_snapshot(_snapshot(followup, narration, _assistant("MANUS-D"), is_running=True)) == 1
    assert watcher.sync_snapshot(_snapshot(followup, narration, _assistant("MANUS-DESK"))) == 0
    assert watcher.sync_snapshot({"status": "snapshot_incomplete"}) == 0
    assert watcher.sync_snapshot(_snapshot(followup, _assistant("MANUS-DESKTOP"))) == 0
    resolved = _snapshot(
        _user("Older unseen prompt"),
        _assistant("Earlier unseen reply"),
        *previous,
        followup,
        _assistant("MANUS-DESKTOP-FOURTH"),
        task_status="completed",
    )
    assert watcher.sync_snapshot(resolved) == 1
    assert watcher.sync_snapshot(resolved) == 0
    assert [_event(record)["content"] for record in _records_for_task(store)] == [
        "Start",
        "First",
        "Use the terminal",
        "MANUS-DESKTOP-FOURTH",
    ]


def test_new_turn_holds_unconfirmed_reply_even_if_thinking_was_never_seen(tmp_path):
    store = TraceStore(tmp_path / "traces.db")
    watcher = ManusWatcher(store)
    settled = (_user("Continue"), _assistant("First result"))
    assert watcher.sync_snapshot(_snapshot(*settled, task_status="completed")) == 2
    assert watcher.sync_snapshot(_snapshot(_user("Old viewport"), _assistant("Old result"))) == 0

    followup = _user("Continue")
    interim = _assistant("I'll use the terminal.")
    assert watcher.sync_snapshot(_snapshot(*settled, followup, interim)) == 1
    assert [_event(record)["content"] for record in _records_for_task(store)] == [
        "Continue",
        "First result",
        "Continue",
    ]
    assert "status" not in _records_for_task(store)[-1]["response"]["body"]["manus_task"]

    restarted = ManusWatcher(TraceStore(store.db_path))
    assert restarted.sync_snapshot(_snapshot(followup, interim, _assistant("MANUS-DESK"))) == 0
    assert len(_records_for_task(store)) == 3
    final = _snapshot(*settled, followup, _assistant("MANUS-DESKTOP-FIFTH"), task_status="completed")
    assert restarted.sync_snapshot(final) == 1
    assert restarted.sync_snapshot(final) == 0
    assert [_event(record)["content"] for record in _records_for_task(store)] == [
        "Continue",
        "First result",
        "Continue",
        "MANUS-DESKTOP-FIFTH",
    ]


def test_unavailable_task_or_empty_chat_does_not_make_a_session(tmp_path):
    store = TraceStore(tmp_path / "traces.db")
    watcher = ManusWatcher(store)
    assert watcher.sync_snapshot({"status": "no_task"}) == 0
    assert watcher.sync_snapshot(_snapshot(_user("Bad ID"), task_id="../invalid")) == 0
    assert watcher.sync_snapshot(_snapshot(_text("Unknown author"))) == 0
    assert store.count_session_rows() == 0


def test_storage_failure_keeps_session_creation_atomic_and_retries_saved_prefix(tmp_path, monkeypatch):
    store = TraceStore(tmp_path / "traces.db")
    watcher = ManusWatcher(store)
    snapshot = _snapshot(_user("Start"), _assistant("End"))
    append_record = store._append_record
    fail_at = 1

    def failing_append(conn, session_id, record):
        append_record(conn, session_id, record)
        if record["turn"] == fail_at:
            raise sqlite3.OperationalError("storage unavailable")

    monkeypatch.setattr(store, "_append_record", failing_append)
    with pytest.raises(sqlite3.OperationalError, match="storage unavailable"):
        watcher.sync_snapshot(snapshot)
    assert store.count_session_rows() == 0

    fail_at = 2
    with pytest.raises(sqlite3.OperationalError, match="storage unavailable"):
        watcher.sync_snapshot(snapshot)
    assert [_event(record)["content"] for record in _records_for_task(store)] == ["Start"]

    fail_at = None
    assert watcher.sync_snapshot(snapshot) == 1
    assert [_event(record)["content"] for record in _records_for_task(store)] == ["Start", "End"]


def test_two_watchers_do_not_duplicate_records_or_sessions(tmp_path, monkeypatch):
    first_store = TraceStore(tmp_path / "traces.db")
    second_store = TraceStore(first_store.db_path)
    first = ManusWatcher(first_store)
    second = ManusWatcher(second_store)
    ready = Barrier(2, timeout=5)

    def wrap_append(store):
        append = store.append_imported_record

        def wait_and_append(*args, **kwargs):
            if kwargs["expected_record_count"] == 0:
                ready.wait()
            return append(*args, **kwargs)

        monkeypatch.setattr(store, "append_imported_record", wait_and_append)

    wrap_append(first_store)
    wrap_append(second_store)
    snapshot = _snapshot(_user("Start"), _assistant("End"))
    with ThreadPoolExecutor(2) as pool:
        results = [pool.submit(watcher.sync_snapshot, snapshot) for watcher in (first, second)]
        assert sorted(result.result() for result in results) == [0, 2]
    assert first_store.count_session_rows() == 1
    assert [_event(record)["content"] for record in _records_for_task(first_store)] == ["Start", "End"]
