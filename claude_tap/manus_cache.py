"""Read terminal events for one open task from Manus Studio's local cache."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path

_TASK_ID = re.compile(r"[A-Za-z0-9]{22}")
_MAX_EVENT_BYTES = 2 * 1024 * 1024
_MAX_READ_BYTES = 16 * 1024 * 1024
_MAX_EVENTS = 2048
_READ_TIMEOUT = 0.75
_MAX_TIMESTAMP_MS = 253_402_300_799_999  # Last UTC millisecond representable by datetime.
_TERMINAL_CONTROL = re.compile(
    r"(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]"  # CSI, including colors and cursor controls.
    r"|(?:\x1b\]|\x9d)[^\x07\x1b\x9c\x9d]*(?:\x07|\x1b\\|\x9c)"  # OSC, including terminal hyperlinks.
)

_SCOPES = """
SELECT DISTINCT scope FROM session_view_event
WHERE CASE WHEN json_valid(scope) THEN json_extract(scope, '$[2]') END = ?
LIMIT 2
"""

_TERMINAL_ROWS = """
SELECT id, payload FROM session_view_event
WHERE scope = ? AND CASE
    WHEN bytes BETWEEN 1 AND ? AND length(CAST(payload AS BLOB)) <= ? AND json_valid(payload)
    THEN json_extract(payload, '$.type') = 'toolUsed'
         AND json_extract(payload, '$.tool') = 'terminal'
END
LIMIT ?
"""


def read_terminal_events(task_id: str, cache_path: Path | None = None) -> list[dict[str, str | int]]:
    """Read finished terminal commands and outputs for the exact AX task ID."""
    if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
        return []
    try:
        if cache_path is None:
            cache_path = Path.home() / "Library" / "Application Support" / "Manus" / "DbCache" / "manus-db-cache.sqlite"
        uri = cache_path.absolute().as_uri() + "?mode=ro"
        deadline = time.monotonic() + _READ_TIMEOUT
        with closing(sqlite3.connect(uri, uri=True, timeout=0.2)) as connection:
            connection.set_progress_handler(lambda: time.monotonic() >= deadline, 2000)
            connection.execute("PRAGMA query_only = ON")
            connection.execute("BEGIN")
            scopes = connection.execute(_SCOPES, (task_id,)).fetchall()
            if len(scopes) != 1:
                return []
            (scope,) = scopes[0]
            if not isinstance(scope, str):
                return []
            group = json.loads(scope)
            if not isinstance(group, list) or len(group) != 3 or not all(isinstance(part, str) for part in group):
                return []
            rows = connection.execute(_TERMINAL_ROWS, (scope, _MAX_EVENT_BYTES, _MAX_EVENT_BYTES, _MAX_EVENTS + 1))
            return _decode_rows(rows)
    except (OSError, RuntimeError, ValueError, sqlite3.Error):
        return []


def _decode_rows(rows: sqlite3.Cursor) -> list[dict[str, str | int]]:
    events: list[tuple[int, str, dict[str, str | int]]] = []
    seen_ids: set[str] = set()
    bytes_read = 0
    for count, (event_id, raw) in enumerate(rows, 1):
        if not isinstance(raw, str) or not isinstance(event_id, str) or not event_id:
            return []
        bytes_read += len(raw.encode("utf-8"))
        if count > _MAX_EVENTS or bytes_read > _MAX_READ_BYTES:
            return []
        if event_id in seen_ids:
            return []
        seen_ids.add(event_id)
        event = json.loads(raw)
        if not isinstance(event, dict) or event.get("type") != "toolUsed" or event.get("tool") != "terminal":
            continue
        timestamp = event.get("timestamp")
        if event.get("id") != event_id or type(timestamp) is not int or not 0 <= timestamp <= _MAX_TIMESTAMP_MS:
            continue
        detail = event.get("detail")
        terminal = detail.get("terminal") if isinstance(detail, dict) else None
        if not isinstance(terminal, dict) or terminal.get("finished") is not True:
            continue
        command, output = terminal.get("command"), terminal.get("output")
        if not isinstance(command, str) or not command.strip() or not isinstance(output, list):
            continue
        if not all(isinstance(line, str) for line in output):
            continue
        events.append(
            (
                timestamp,
                event_id,
                {
                    "id": event_id,
                    "type": "tool_call",
                    "tool_name": "terminal",
                    "command": command,
                    "output": _TERMINAL_CONTROL.sub("", "\n".join(output)),
                    "timestamp_ms": timestamp,
                },
            )
        )
    events.sort(key=lambda event: (event[0], event[1]))
    return [event for _, _, event in events]
