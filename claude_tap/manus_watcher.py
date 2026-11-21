"""Import user-visible messages from Manus Studio's open task into the trace store."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlsplit, urlunsplit
from uuid import NAMESPACE_URL, uuid5

from claude_tap.trace_store import TraceStore

_CLIENT = "manus"
_MODE = "manus-desktop"
_TASK_ID = re.compile(r"[A-Za-z0-9]{22}")
_WEB_URL = re.compile(r"""https?://[^\s<>\[\]()"']+""", re.IGNORECASE)
_URL_SECRET_KEYS = {"access_token", "api_key", "apikey", "auth", "credential", "key", "sig", "signature", "token"}
_ASSISTANT_CLASSES = {"whitespace-pre-wrap", "u-break-words"}
_TEXT_EXCLUDED_ROLES = {"AXButton", "AXTextArea", "AXTextField", "AXMenu", "AXImage", "AXSecureTextField"}


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


def _safe_text(value: str) -> str:
    return _WEB_URL.sub(_safe_url, value.encode("utf-8", errors="replace").decode("utf-8"))


def _nodes(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
    children = node.get("children")
    if isinstance(children, list):
        yield from (child for child in children if isinstance(child, dict))


def _classes(node: dict[str, Any]) -> set[str]:
    classes = node.get("dom_classes")
    if isinstance(classes, str):
        return set(classes.split())
    return {item for item in classes if isinstance(item, str)} if isinstance(classes, list) else set()


def _visible_text(node: dict[str, Any]) -> Iterator[str]:
    role = node.get("role")
    if role in _TEXT_EXCLUDED_ROLES:
        return
    if role == "AXStaticText":
        for field in ("value", "title", "description"):
            text = node.get(field)
            if isinstance(text, str) and text.strip():
                yield text.strip()
                return
    for child in _nodes(node):
        yield from _visible_text(child)


def _chat_body(node: dict[str, Any]) -> Iterator[str]:
    if "chat-message-body" in _classes(node):
        yield from _visible_text(node)
        return
    for child in _nodes(node):
        yield from _chat_body(child)


@dataclass(frozen=True)
class _Message:
    role: str
    text: str


def _messages(node: dict[str, Any]) -> Iterator[_Message]:
    classes = _classes(node)
    if "user-message-bubble-new" in classes:
        text = "\n".join(_chat_body(node))
        if text:
            yield _Message("user", _safe_text(text))
    elif _ASSISTANT_CLASSES <= classes:
        text = "\n".join(_visible_text(node))
        if text:
            yield _Message("assistant", _safe_text(text))
    elif node.get("role") == "AXGroup":
        for child in _nodes(node):
            yield from _messages(child)


def _stored_message(record: dict[str, Any]) -> _Message:
    event = record["response"]["body"]["manus_event"]
    return _Message(event["role"], event["content"])


def _overlap(stored: list[_Message], observed: list[_Message]) -> tuple[int, bool]:
    """Locate the end of persisted messages within this snapshot."""
    if len(stored) > 1 and len(observed) > len(stored) and observed[: len(stored)] != stored:
        # The viewport sometimes loads earlier turns. Only skip past a unique,
        # intact match of everything we already recorded.
        matches = [
            start + len(stored)
            for start in range(1, len(observed) - len(stored) + 1)
            if observed[start : start + len(stored)] == stored
        ]
        if len(matches) == 1:
            return matches[0], False
    for size in range(min(len(stored), len(observed)), 0, -1):
        if stored[-size:] == observed[:size]:
            return size, False
        stored_last = stored[-1]
        observed_last = observed[size - 1]
        if (
            stored_last.role == observed_last.role == "assistant"
            and observed_last.text.startswith(stored_last.text)
            and stored[-size:-1] == observed[: size - 1]
            and (size > 1 or len(stored) == 1)
        ):
            return size, True
    return 0, False


def _record(task: dict[str, str], message: _Message, position: int, *, reply_pending: bool = False) -> dict[str, Any]:
    task_id = task["id"]
    source_id = f"{task_id}-{position}"
    user_message = [{"role": "user", "content": message.text}] if message.role == "user" else []
    assistant_content = [{"type": "text", "text": message.text}] if message.role == "assistant" else []
    return {
        "request_id": f"manus-desktop-{source_id}",
        "transport": "manus-task",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "capture": {
            "client": _CLIENT,
            "proxy_mode": _MODE,
            "manus_task_id": task_id,
            "timestamp_kind": "observed",
            **({"manus_reply_pending": True} if reply_pending else {}),
        },
        "request": {"method": "MANUS_EVENT", "path": f"/manus/tasks/{task_id}", "body": {"messages": user_message}},
        "response": {
            "body": {
                "content": assistant_content,
                "manus_task": task,
                "manus_event": {
                    "id": source_id,
                    "type": f"{message.role}_message",
                    "role": message.role,
                    "content": message.text,
                },
            }
        },
    }


class ManusWatcher:
    """Merge snapshots of the currently opened Manus Studio task into local sessions."""

    def __init__(self, store: TraceStore) -> None:
        self._store = store
        self._session_ids: set[str] = set()
        self._last_idle_task_id: str | None = None
        self._running_task_id: str | None = None

    @property
    def session_ids(self) -> set[str]:
        """Local session IDs to protect from trace retention during a watch."""
        return self._session_ids.copy()

    def sync_snapshot(self, snapshot: dict[str, Any]) -> int:
        """Import newly visible messages and reconcile a growing final reply."""
        task_id = snapshot.get("task_id")
        conversation = snapshot.get("conversation")
        if (
            snapshot.get("status") != "ok"
            or not isinstance(task_id, str)
            or not _TASK_ID.fullmatch(task_id)
            or not isinstance(conversation, dict)
        ):
            if snapshot.get("status") in ("app_not_running", "no_task"):
                self._last_idle_task_id = None
            if snapshot.get("status") == "app_not_running":
                self._running_task_id = None
            return 0
        previous_idle_task_id = self._last_idle_task_id
        self._last_idle_task_id = None
        phase = snapshot.get("is_running")
        status = snapshot.get("task_status")
        if phase is False or status == "completed":
            self._running_task_id = None
        elif phase is True:
            self._running_task_id = task_id
        elif self._running_task_id != task_id:
            self._running_task_id = None
        observed = list(_messages(conversation))
        if not observed:
            return 0

        task = {"id": task_id}
        if isinstance(title := snapshot.get("title"), str) and title.strip():
            task["title"] = _safe_text(title.strip())
        if status in ("thinking", "completed"):
            task["status"] = status
        session_id = str(uuid5(NAMESPACE_URL, f"https://manus.im/app/{task_id}#desktop"))
        saved = self._store.load_records(session_id)
        stored = [_stored_message(record) for record in saved]
        overlap, grew = _overlap(stored, observed)
        if saved and phase is None and status != "completed" and self._running_task_id is None:
            latest = saved[-1]
            settled = (
                stored[-1].role == "assistant" and latest["response"]["body"]["manus_task"].get("status") == "completed"
            )
            # A user message beyond a matching settled turn starts a new reply,
            # even if the entire positive thinking indicator fell between polls.
            anchored_new_user = overlap and any(message.role == "user" for message in observed[overlap:])
            if latest["capture"].get("manus_reply_pending") or (settled and anchored_new_user):
                self._running_task_id = task_id
        running = self._running_task_id == task_id
        # All prose following the latest user message can still change or disappear
        # while Manus is working, even when its thinking indicator vanishes.
        if running and observed[-1].role == "assistant":
            while observed and observed[-1].role == "assistant":
                observed.pop()
            if not observed:
                return 0
            overlap, grew = _overlap(stored, observed)
        if stored:
            self._session_ids.add(session_id)
            # Studio can replace the entire visible row set at the start of a follow-up.
            # A new user message next to an active run anchors a disjoint viewport.
            starts_followup = (
                previous_idle_task_id == task_id
                and running
                and len(observed) == 1
                and observed[0].role == "user"
                and observed[0] not in stored
            )
            if not overlap and not starts_followup:
                return 0
        metadata_only_update = (
            bool(overlap)
            and overlap == len(observed)
            and (
                ("status" in task and saved[-1]["response"]["body"]["manus_task"].get("status") != status)
                or saved[-1]["capture"].get("manus_reply_pending", False) != running
            )
        )
        if grew or metadata_only_update:
            record = _record(task, observed[overlap - 1], len(stored), reply_pending=running)
            if not self._store.replace_latest_imported_record(
                session_id,
                record,
                expected_record_count=len(stored),
                expected_record=saved[-1],
            ):
                return 0
        imported = 0
        for index, message in enumerate(observed[overlap:], start=overlap):
            if not self._store.append_imported_record(
                session_id,
                _record(
                    task, message, len(stored) + imported + 1, reply_pending=running and index == len(observed) - 1
                ),
                expected_record_count=len(stored) + imported,
                client=_CLIENT,
                proxy_mode=_MODE,
            ):
                break
            self._session_ids.add(session_id)
            imported += 1
        if not running and observed[-1].role == "assistant":
            self._last_idle_task_id = task_id
        return imported

    def close(self) -> None:
        """Finalize local sessions after polling stops."""
        for session_id in self._session_ids:
            self._store.finalize_session(session_id)
