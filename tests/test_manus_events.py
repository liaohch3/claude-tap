"""Contracts for recording Manus Studio task events from its live stream."""

import json

from claude_tap.manus_events import ManusEventRecorder, is_manus_event_stream
from claude_tap.trace_store import TraceStore

_TASK_ID = "0123456789abcdefghijkl"
_OTHER_TASK_ID = "9876543210zyxwvutsrqpo"
_T0 = 1795269800000


def _packet(event, task_id=_TASK_ID, prefix="42"):
    message = {"id": "envelope", "type": "event", "sessionId": task_id, "timestamp": _T0, "event": event}
    return prefix + json.dumps(["message", message], ensure_ascii=False)


def _chat(event_id, sender, content, offset=0):
    return {"id": event_id, "type": "chat", "sender": sender, "content": content, "timestamp": _T0 + offset}


def _terminal(event_id, status, command=None, output=None, offset=0):
    event = {
        "id": event_id,
        "type": "toolUsed",
        "tool": "terminal",
        "status": status,
        "brief": "Run the probe",
        "message": {"action": "execute", "param": command or "echo probe"},
        "timestamp": _T0 + offset,
    }
    if output is not None:
        event["detail"] = {
            "terminal": {"action": "execute", "command": command, "output": output, "finished": True, "shellId": "main"}
        }
    return event


def _records(store, task_id=_TASK_ID):
    for row in store.list_session_rows():
        records = store.load_records(row["id"])
        if records[0]["capture"]["manus_task_id"] == task_id:
            return records
    raise AssertionError(f"No session for {task_id}")


def _events(store, task_id=_TASK_ID):
    return [record["response"]["body"]["manus_event"] for record in _records(store, task_id)]


def test_records_a_live_task_in_arrival_order_with_complete_tool_output(tmp_path):
    store = TraceStore(tmp_path / "traces.db")
    recorder = ManusEventRecorder(store)
    stream = [
        _chat("user1", "user", "Run the probe\nthen stop"),
        {"id": "status1", "type": "statusUpdate", "agentStatus": "running", "brief": "Working", "timestamp": _T0 + 1},
        {"id": "plan1", "type": "planUpdate", "tasks": [{"title": "Run probe", "status": "doing"}], "timestamp": _T0},
        {"id": "step1", "type": "newPlanStep", "stepId": "s", "title": "Run probe", "timestamp": _T0 + 2},
        {"id": "live1", "type": "liveStatus", "text": "Thinking"},
        {"id": "delta1", "type": "chatDelta", "delta": {"content": "I w"}, "targetEventId": "assistant1"},
        _terminal("tool1", "argumentsFinished", offset=3),
        {"id": "sandbox1", "type": "sandboxUpdate", "status": "running", "vncUrl": "https://vnc.test/?token=PRIVATE"},
        _terminal("tool1", "streaming", offset=3),
        _terminal("tool1", "success", "echo probe", ["\x1b[32m$ \x1b[0mprobe", "done\x1b[K"], offset=3),
        {
            "id": "tool2",
            "type": "toolUsed",
            "tool": "text_editor",
            "status": "success",
            "message": {"action": "read", "param": "/tmp/probe.txt"},
            "detail": {"textEditor": {"action": "read", "path": "/tmp/probe.txt", "content": "file body"}},
            "timestamp": _T0 + 4,
        },
        _chat("assistant1", "assistant", "file body", offset=5),
        _chat("assistant1", "assistant", "file body", offset=5),
        {"id": "status2", "type": "statusUpdate", "agentStatus": "stopped", "timestamp": _T0 + 6},
        {"id": "tool3", "type": "toolUsed", "tool": "suggestion", "status": "success", "detail": {"suggestion": {}}},
    ]

    assert sum(recorder.feed(_packet(event)) for event in stream) == 8
    assert sum(recorder.feed(_packet(event)) for event in stream) == 0

    records = _records(store)
    events = [record["response"]["body"]["manus_event"] for record in records]
    assert [event["type"] for event in events] == [
        "user_message",
        "status",
        "plan",
        "plan_step",
        "tool_used",
        "tool_used",
        "assistant_message",
        "status",
    ]
    assert [record["turn"] for record in records] == list(range(1, 9))
    # The streamed tool call is one record that ends with its final output.
    assert events[4] == {
        "id": "tool1",
        "type": "tool_used",
        "tool": "terminal",
        "status": "success",
        "brief": "Run the probe",
        "input": {
            "action": "execute",
            "param": "echo probe",
            "command": "echo probe",
            "finished": True,
            "shellId": "main",
        },
        "output": "$ probe\ndone",
    }
    assert events[5]["input"] == {"action": "read", "param": "/tmp/probe.txt", "path": "/tmp/probe.txt"}
    assert events[5]["output"] == "file body"
    assert events[2]["steps"] == [{"title": "Run probe", "status": "doing"}]
    assert records[0]["request"]["body"]["messages"] == [{"role": "user", "content": "Run the probe\nthen stop"}]
    assert records[6]["response"]["body"]["content"] == [{"type": "text", "text": "file body"}]
    assert records[0]["timestamp"].startswith("2026-11-21T14:03:20")
    assert records[0]["capture"] == {
        "client": "manus",
        "proxy_mode": "manus-desktop",
        "manus_task_id": _TASK_ID,
        "manus_source": "event-stream",
        "timestamp_kind": "event",
    }
    assert records[-1]["response"]["body"]["manus_task"] == {
        "id": _TASK_ID,
        "title": "Run the probe",
        "status": "stopped",
    }
    assert "PRIVATE" not in json.dumps(records)
    [session] = store.list_session_rows()
    assert (session["client"], session["proxy_mode"], session["record_count"]) == ("manus", "manus-desktop", 8)
    assert recorder.session_ids == {session["id"]}


def test_maps_each_tool_detail_to_input_and_output(tmp_path):
    store = TraceStore(tmp_path / "traces.db")
    recorder = ManusEventRecorder(store)

    def tool(event_id, name, detail, **extra):
        return {"id": event_id, "type": "toolUsed", "tool": name, "status": "success", "detail": detail, **extra}

    stream = [
        tool("write", "text_editor", {"textEditor": {"action": "write", "path": "/tmp/a", "content": "new text"}}),
        tool(
            "web",
            "webpage_extract",
            {"webpageExtract": {"action": "extract", "url": "https://a.test", "markdown": "# A"}},
        ),
        tool(
            "mcp", "mcp", {"mcp": {"server": "github", "tool": "list", "argumentsPreview": "{}", "resultPreview": "[]"}}
        ),
        tool("search", "search", {"search": {"queries": ["claude tap"], "results": [{"title": "Repo"}]}}),
        tool(
            "omitted", "terminal", {"terminal": {"command": "ls", "output": [], "finished": True}}, detailOmitted=True
        ),
        tool("unknown", "media_viewer", {"mediaViewer": {"filePath": "/tmp/a.png", "type": "image"}}),
    ]
    assert sum(recorder.feed(_packet(event)) for event in stream) == 6

    write, web, mcp, search, omitted, unknown = _events(store)
    assert write["input"] == {"action": "write", "path": "/tmp/a", "content": "new text"}
    assert "output" not in write
    assert (web["input"], web["output"]) == ({"action": "extract", "url": "https://a.test"}, "# A")
    assert (mcp["input"]["tool"], mcp["output"]) == ("list", "[]")
    assert (search["input"], search["output"]) == ({"queries": ["claude tap"]}, [{"title": "Repo"}])
    assert omitted["detail_omitted"] is True
    assert "output" not in omitted
    assert unknown["input"] == {"filePath": "/tmp/a.png", "type": "image"}


def test_masks_recognizable_credentials_and_signed_urls_before_storage(tmp_path):
    store = TraceStore(tmp_path / "traces.db")
    recorder = ManusEventRecorder(store)
    command = (
        'OPENAI_API_KEY="secret alpha" MAX_TOKENS=40 curl '
        "-H 'Authorization: Bearer secret-beta' -H 'X-API-Key: secret-gamma' "
        '-H "Authorization: Bearer ${ACCESS_TOKEN}" -H "Api-Key: $(read_credential)" https://example.test'
    )
    output = [
        "Proxy-Authorization: Basic secret-delta",
        "GITHUB_TOKEN=secret-epsilon",
        "DATABASE_PASSWORD='secret zeta'",
        "OPENAI_API_KEY=$OPENAI_API_KEY",
        "TOKEN=$(read_credential)",
        "MAX_TOKENS=40",
        "https://files.test/report.pdf?X-Amz-Signature=secret-eta&page=2",
        "The key number remains meaningful.",
    ]
    assert recorder.feed(_packet(_terminal("tool", "success", command, output))) == 1

    [record] = _records(store)
    event = record["response"]["body"]["manus_event"]
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
        "MAX_TOKENS=40\n"
        "https://files.test/report.pdf\n"
        "The key number remains meaningful."
    )
    assert "secret" not in json.dumps(record)


def test_keeps_tasks_apart_and_resumes_after_restart_without_duplicates(tmp_path):
    store = TraceStore(tmp_path / "traces.db")
    first = ManusEventRecorder(store)
    assert first.feed(_packet(_chat("u1", "user", "First task"))) == 1
    assert first.feed(_packet(_terminal("t1", "streaming"))) == 1
    assert first.feed(_packet(_chat("u2", "user", "Second task"), task_id=_OTHER_TASK_ID)) == 1
    first.close()

    restarted = ManusEventRecorder(store)
    assert restarted.feed(_packet(_chat("u1", "user", "First task"))) == 0
    assert restarted.feed(_packet(_terminal("t1", "success", "echo probe", ["probe"]))) == 0
    assert restarted.feed(_packet(_chat("a1", "assistant", "Done"))) == 1

    assert [event["type"] for event in _events(store)] == ["user_message", "tool_used", "assistant_message"]
    assert _events(store)[1]["output"] == "probe"
    assert _records(store)[-1]["response"]["body"]["manus_task"]["title"] == "First task"
    assert [event["content"] for event in _events(store, _OTHER_TASK_ID)] == ["Second task"]
    assert len(store.list_session_rows()) == 2


def test_two_recorders_on_one_task_do_not_duplicate_or_lose_updates(tmp_path):
    store = TraceStore(tmp_path / "traces.db")
    first, second = ManusEventRecorder(store), ManusEventRecorder(store)
    assert first.feed(_packet(_chat("u1", "user", "Shared task"))) == 1
    assert second.feed(_packet(_chat("u1", "user", "Shared task"))) == 0
    assert second.feed(_packet(_terminal("t1", "streaming"))) == 1
    # The first recorder's cached count is stale; it reloads and still applies the update.
    assert first.feed(_packet(_terminal("t1", "success", "echo probe", ["probe"]))) == 0
    assert first.feed(_packet(_chat("a1", "assistant", "Done"))) == 1

    events = _events(store)
    assert [event["id"] for event in events] == ["u1", "t1", "a1"]
    assert events[1]["output"] == "probe"


def test_reads_the_batch_of_events_missed_before_joining_a_task(tmp_path):
    store = TraceStore(tmp_path / "traces.db")
    recorder = ManusEventRecorder(store)
    history = {"type": "sessions_synced", "results": [{"sessionId": _TASK_ID, "events": [_chat("old", "user", "x")]}]}
    # Task history loaded at startup is not live activity.
    assert recorder.feed("42" + json.dumps(["message", history])) == 0
    batch = {
        "id": "batch",
        "type": "eventsNotifyEventsAfter",
        "events": [_chat("u1", "user", "Long opening message"), "malformed", _chat("a1", "assistant", "Reply")],
    }
    # Socket.IO may prefix a namespace and an acknowledgement id.
    assert recorder.feed(_packet(batch, prefix="42/chat,17")) == 2

    assert [event["id"] for event in _events(store)] == ["u1", "a1"]


def test_ignores_messages_that_are_not_task_events(tmp_path):
    store = TraceStore(tmp_path / "traces.db")
    recorder = ManusEventRecorder(store)
    chat = _chat("u1", "user", "hello")
    ignored = [
        "2",
        '40{"sid":"abc"}',
        "42[not json",
        '42["notification",{"type":"event"}]',
        '42["message","text"]',
        _packet(chat, task_id=""),
        _packet(chat, task_id="../../etc/passwd"),
        _packet({**chat, "id": ""}),
        _packet({**chat, "id": None}),
        _packet({**chat, "sender": "system"}),
        _packet({**chat, "content": "   "}),
        _packet({"id": "s", "type": "statusUpdate", "agentStatus": ""}),
        _packet({"id": "t", "type": "toolUsed", "tool": None}),
        _packet({"id": "b", "type": "eventsNotifyEventsAfter", "events": None}),
        "42" + json.dumps(["message", {"type": "event", "sessionId": _TASK_ID, "event": "text"}]),
    ]
    assert [recorder.feed(text) for text in ignored] == [0] * len(ignored)
    assert store.list_session_rows() == []
    assert recorder.session_ids == set()


def test_only_the_manus_task_socket_is_treated_as_the_event_stream():
    assert is_manus_event_stream("api.manus.im", "/socket.io/?EIO=4&transport=websocket")
    assert is_manus_event_stream("API.Manus.im", "/socket.io/")
    assert not is_manus_event_stream("api.manus.im", "/notifier-v2-socket-io/?EIO=4")
    assert not is_manus_event_stream("api.manus.im.example.test", "/socket.io/")
    assert not is_manus_event_stream("notmanus.im", "/socket.io/")
