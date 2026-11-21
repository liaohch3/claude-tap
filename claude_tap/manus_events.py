"""Record Manus Studio task events from its live socket.io stream."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlsplit, urlunsplit
from uuid import NAMESPACE_URL, uuid5

from claude_tap.trace_store import TraceStore

log = logging.getLogger("claude-tap")

_CLIENT = "manus"
_MODE = "manus-desktop"
_TASK_ID = re.compile(r"[A-Za-z0-9]{22}")
# Engine.IO "message" (4) carrying a Socket.IO EVENT (2), with an optional
# namespace and acknowledgement id ahead of the JSON payload.
_SOCKET_IO_EVENT = re.compile(r"42(?:/[^,\[]*,)?\d*(\[.*)", re.DOTALL)
_WEB_URL = re.compile(r"""https?://[^\s<>\[\]()"']+""", re.IGNORECASE)
_URL_SECRET_KEYS = {"access_token", "api_key", "apikey", "auth", "credential", "key", "sig", "signature", "token"}
_HEADER_SECRET = re.compile(
    r"""(?ix)(?P<prefix>\b(?:authorization|proxy-authorization)["']?[ \t]*:[ \t]*["']?(?:bearer|basic)[ \t]+
    | \b(?:x-api-key|api-key|x-auth-token|x-access-token)["']?[ \t]*:[ \t]*["']?)
    (?P<value>(?!\$)[^\s"'`<>\\,;|&]+)"""
)
_SHELL_SECRET_ASSIGNMENT = re.compile(
    r"""(?ix)(?P<prefix>(?<![\w.-])(?: (?:[a-z_]\w*_)?(?:api_key|apikey|access_token|auth_token|bearer_token
    |refresh_token|session_token|client_secret|secret_access_key|secret_key|private_key|password|passwd)
    | token | secret | github_token | gitlab_token | gh_token | ci_job_token )=)
    (?!\$\()(?P<value>"(?:\\[^\r\n]|[^"\\\r\n])*"|'[^'\r\n]*'|(?:\\[^\r\n]|[^\s"'`;|&<>])+ )"""
)
_SHELL_VARIABLE = re.compile(r"\$(?:[A-Za-z_]\w*|\{[A-Za-z_]\w*\})")
# Terminal colour and cursor sequences, which only make sense to a terminal.
_ANSI_SEQUENCE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_TITLE_LENGTH = 80
# The detail field holding what a tool returned; every other field is input.
_TOOL_OUTPUT_FIELDS = {
    "terminal": "output",
    "textEditor": "content",
    "webpageExtract": "markdown",
    "mcp": "resultPreview",
    "search": "results",
    "searchImage": "results",
}


def is_manus_event_stream(hostname: str, path: str) -> bool:
    """Return True for the WebSocket that carries Manus task events."""
    host = hostname.lower()
    return (host == "manus.im" or host.endswith(".manus.im")) and path.startswith("/socket.io/")


def _safe_url(match: re.Match[str]) -> str:
    value = match.group()
    try:
        url = urlsplit(value)
    except ValueError:
        return "[link omitted]"
    has_signature = any(
        key.lower() in _URL_SECRET_KEYS or key.lower().startswith(("x-amz-", "x-goog-"))
        for key, _ in parse_qsl(url.query)
    )
    if has_signature or url.username is not None:
        return urlunsplit((url.scheme, url.netloc.rsplit("@", 1)[-1], url.path, "", ""))
    return value


def _redact_secret(match: re.Match[str]) -> str:
    value = match["value"]
    quote = value[0] if value[0] in "\"'" and value[-1] == value[0] else ""
    literal = value[1:-1] if quote else value
    if not literal or _SHELL_VARIABLE.fullmatch(literal):
        return match.group()
    return f"{match['prefix']}{quote}REDACTED{quote}"


def _safe_text(value: str) -> str:
    """Drop signed URL queries and mask recognizable literal credentials."""
    value = _WEB_URL.sub(_safe_url, value.encode("utf-8", errors="replace").decode("utf-8"))
    return _SHELL_SECRET_ASSIGNMENT.sub(_redact_secret, _HEADER_SECRET.sub(_redact_secret, value))


def _safe_value(value: Any) -> Any:
    if isinstance(value, str):
        return _safe_text(value)
    if isinstance(value, list):
        return [_safe_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _safe_value(item) for key, item in value.items()}
    return value


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _tool_event(event: dict[str, Any]) -> dict[str, Any] | None:
    tool = _text(event.get("tool"))
    # Follow-up prompt suggestions are interface hints, not work the agent did.
    if not tool or tool == "suggestion":
        return None
    inputs: dict[str, Any] = {}
    message = event.get("message")
    if isinstance(message, dict):
        inputs.update({key: message[key] for key in ("action", "param") if message.get(key) not in (None, "")})
    output: Any = None
    detail = event.get("detail")
    kind, body = next(iter(detail.items()), (None, None)) if isinstance(detail, dict) else (None, None)
    if isinstance(body, dict):
        output_field = _TOOL_OUTPUT_FIELDS.get(kind)
        if kind == "textEditor" and body.get("action") != "read":
            # Written text is what the agent supplied, not what the tool returned.
            output_field = None
        for key, value in body.items():
            if key == output_field:
                output = value
            else:
                inputs[key] = value
    if isinstance(output, list) and all(isinstance(line, str) for line in output):
        output = "\n".join(output)
    if isinstance(output, str):
        output = _ANSI_SEQUENCE.sub("", output)
    normalized: dict[str, Any] = {"type": "tool_used", "tool": tool, "input": inputs}
    for key in ("status", "brief", "description"):
        if text := _text(event.get(key)):
            normalized[key] = text
    if output not in (None, "", []):
        normalized["output"] = output
    if event.get("detailOmitted") is True:
        normalized["detail_omitted"] = True
    return normalized


def _normalize(event: dict[str, Any]) -> dict[str, Any] | None:
    """Reduce a Manus event to the fields worth keeping, or None to skip it."""
    kind = event.get("type")
    normalized: dict[str, Any] | None = None
    if kind == "chat":
        role = event.get("sender")
        content = _text(event.get("content"))
        if role in ("user", "assistant") and content:
            normalized = {"type": f"{role}_message", "role": role, "content": content}
    elif kind == "toolUsed":
        normalized = _tool_event(event)
    elif kind == "statusUpdate":
        if status := _text(event.get("agentStatus")):
            normalized = {"type": "status", "agent_status": status}
            for key in ("brief", "description"):
                if text := _text(event.get(key)):
                    normalized[key] = text
    elif kind == "newPlanStep":
        if title := _text(event.get("title")):
            normalized = {"type": "plan_step", "title": title}
    elif kind == "planUpdate":
        tasks = event.get("tasks")
        steps = [
            {"title": title, "status": _text(task.get("status"))}
            for task in (tasks if isinstance(tasks, list) else [])
            if isinstance(task, dict) and (title := _text(task.get("title")))
        ]
        if steps:
            normalized = {"type": "plan", "steps": steps}
    if normalized is None:
        return None
    return _safe_value({"id": event["id"], **normalized})


def _timestamp(event: dict[str, Any]) -> tuple[str, str]:
    milliseconds = event.get("timestamp")
    if type(milliseconds) is int and milliseconds > 0:
        try:
            return datetime.fromtimestamp(milliseconds / 1000, timezone.utc).isoformat(), "event"
        except (OverflowError, OSError, ValueError):
            pass
    return datetime.now(timezone.utc).isoformat(), "observed"


class _Session:
    def __init__(self, session_id: str, task_id: str, saved: list[dict[str, Any]]) -> None:
        self.session_id = session_id
        self.task: dict[str, str] = {"id": task_id}
        self.count = len(saved)
        self.records: dict[str, dict[str, Any]] = {}
        for record in saved:
            try:
                body = record["response"]["body"]
                self.records[body["manus_event"]["id"]] = record
                self.task = {**self.task, **body["manus_task"], "id": task_id}
            except (KeyError, TypeError):
                continue


class ManusEventRecorder:
    """Write one local session per Manus task from the events its app receives."""

    def __init__(self, store: TraceStore) -> None:
        self._store = store
        self._sessions: dict[str, _Session] = {}
        self._written: set[str] = set()

    @property
    def session_ids(self) -> set[str]:
        """Local session IDs to protect from trace retention during a capture."""
        return self._written.copy()

    def feed(self, text: str) -> int:
        """Record the task events in one server-to-app WebSocket message."""
        match = _SOCKET_IO_EVENT.fullmatch(text)
        if match is None:
            return 0
        try:
            packet = json.loads(match.group(1))
        except (ValueError, RecursionError):
            return 0
        if not isinstance(packet, list) or len(packet) < 2 or packet[0] != "message" or not isinstance(packet[1], dict):
            return 0
        message = packet[1]
        event = message.get("event")
        if message.get("type") != "event" or not isinstance(event, dict):
            return 0
        task_id = message.get("sessionId")
        if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
            return 0
        # After joining a task the app receives the events it missed as one batch.
        batch = event.get("events") if event.get("type") == "eventsNotifyEventsAfter" else [event]
        if not isinstance(batch, list):
            return 0
        return sum(self._record(task_id, item) for item in batch if isinstance(item, dict))

    def _session(self, task_id: str) -> _Session:
        session = self._sessions.get(task_id)
        if session is None:
            session_id = str(uuid5(NAMESPACE_URL, f"https://manus.im/app/{task_id}#desktop"))
            session = _Session(session_id, task_id, self._store.load_records(session_id))
            self._sessions[task_id] = session
        return session

    def _record(self, task_id: str, raw: dict[str, Any]) -> int:
        event_id = raw.get("id")
        if not isinstance(event_id, str) or not event_id:
            return 0
        event = _normalize(raw)
        if event is None:
            return 0
        session = self._session(task_id)
        task = dict(session.task)
        # The stream carries no task title, so the opening request names the task.
        if "title" not in task and event["type"] == "user_message":
            task["title"] = event["content"].splitlines()[0][:_TITLE_LENGTH]
        if event["type"] == "status":
            task["status"] = event["agent_status"]
        session.task = task
        record = self._build_record(task, raw, event)
        # Another claude-tap process may have advanced this session; reload once and retry.
        for attempt in range(2):
            existing = session.records.get(event_id)
            if existing is None:
                if self._store.append_imported_record(
                    session.session_id,
                    record,
                    expected_record_count=session.count,
                    client=_CLIENT,
                    proxy_mode=_MODE,
                ):
                    session.count += 1
                    session.records[event_id] = self._stored_form(record, session.count)
                    self._written.add(session.session_id)
                    return 1
            elif existing["response"]["body"]["manus_event"] == event:
                return 0
            elif self._store.replace_imported_record(
                session.session_id,
                record,
                expected_record_count=session.count,
                expected_record=existing,
            ):
                session.records[event_id] = self._stored_form(record, existing["turn"])
                self._written.add(session.session_id)
                return 0
            if attempt == 0:
                self._reload(session)
        log.warning("Manus task session changed while recording; skipped one event.")
        return 0

    @staticmethod
    def _build_record(task: dict[str, str], raw: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
        task_id = task["id"]
        timestamp, timestamp_kind = _timestamp(raw)
        role = event.get("role")
        user_message = [{"role": "user", "content": event["content"]}] if role == "user" else []
        assistant_content = [{"type": "text", "text": event["content"]}] if role == "assistant" else []
        return {
            "request_id": f"manus-desktop-{task_id}-{event['id']}",
            "transport": "manus-task",
            "timestamp": timestamp,
            "capture": {
                "client": _CLIENT,
                "proxy_mode": _MODE,
                "manus_task_id": task_id,
                "manus_source": "event-stream",
                "timestamp_kind": timestamp_kind,
            },
            "request": {"method": "MANUS_EVENT", "path": f"/manus/tasks/{task_id}", "body": {"messages": user_message}},
            "response": {"body": {"content": assistant_content, "manus_task": task, "manus_event": event}},
        }

    @staticmethod
    def _stored_form(record: dict[str, Any], turn: int) -> dict[str, Any]:
        # The store returns records as decoded JSON with their turn assigned.
        return json.loads(json.dumps({**record, "turn": turn}, ensure_ascii=False))

    def _reload(self, session: _Session) -> None:
        fresh = _Session(session.session_id, session.task["id"], self._store.load_records(session.session_id))
        session.count, session.records = fresh.count, fresh.records

    def close(self) -> None:
        """Finalize the local sessions written during this capture."""
        for session_id in self._written:
            self._store.finalize_session(session_id)
