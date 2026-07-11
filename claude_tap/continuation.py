"""Detect continued conversations that were split across trace sessions.

Each ``claude-tap`` launch records a fresh trace session, so one logical
Claude Code conversation resumed with ``claude -c`` (or relaunched after a
``/compact``) ends up split across several stored sessions. Two signals stitch
the pieces back together:

- **prefix link**: the continued session's first main-loop request replays the
  earlier session's message history, so its conversation messages start with
  the earlier session's last-request messages (the in-flight tail message may
  be extended or rewritten on resume).
- **compact link**: after a compaction, the continued session opens with a
  "This session is being continued from a previous conversation" message whose
  embedded summary was produced verbatim by the earlier session's last
  main-loop response.

Both signals additionally require the same client and the same working
directory advertised in the request system prompt.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from typing import Any

from .dashboard import (
    _content_text,
    _is_agent_side_request,
    _is_claude_title_generation_request,
    _record_response_text,
    _request_user_text,
)
from .trace_store import TraceStore

COMPACT_RESUME_MARKER = "This session is being continued from a previous conversation"

# How many records to decode from each end of a session before falling back to
# a full scan. Main-loop requests sit at the edges in practice; the window just
# has to absorb count_tokens probes and title-generation noise around them.
_BOUNDARY_WINDOW = 32

# A continuation must replay every history message except the in-flight tail,
# which Claude Code may extend (new tool results) or rewrite on resume.
_PREFIX_TAIL_TOLERANCE = 1

_CWD_PATTERN = re.compile(r"Primary working directory:\s*(.+)")

_MIN_SUMMARY_PROBE_CHARS = 80
_SUMMARY_PROBE_CHARS = 300

_fingerprint_cache: dict[str, tuple[int, dict[str, Any] | None]] = {}
_fingerprint_cache_lock = threading.Lock()


def reset_fingerprint_cache() -> None:
    """Clear cached session fingerprints (for tests)."""
    with _fingerprint_cache_lock:
        _fingerprint_cache.clear()


def _strip_volatile(value: Any) -> Any:
    """Drop cache_control markers, which move between replays of one history."""
    if isinstance(value, dict):
        return {key: _strip_volatile(item) for key, item in value.items() if key != "cache_control"}
    if isinstance(value, list):
        return [_strip_volatile(item) for item in value]
    return value


def _message_digest(message: Any) -> str:
    payload = json.dumps(_strip_volatile(message), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _conversation_messages(messages: list[Any]) -> list[dict[str, Any]]:
    """Keep user/assistant turns; injected system entries vary between replays."""
    return [
        message for message in messages if isinstance(message, dict) and message.get("role") in ("user", "assistant")
    ]


def _is_main_loop_record(record: dict[str, Any]) -> bool:
    request = record.get("request")
    if not isinstance(request, dict):
        return False
    path = str(request.get("path") or "").lower()
    if "/v1/messages" not in path or "count_tokens" in path:
        return False
    body = request.get("body")
    if not isinstance(body, dict):
        return False
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return False
    if _is_claude_title_generation_request(body):
        return False
    return not _is_agent_side_request(body, _request_user_text(body) or "")


def _record_messages(record: dict[str, Any]) -> list[Any]:
    body = record.get("request", {}).get("body")
    messages = body.get("messages") if isinstance(body, dict) else None
    return messages if isinstance(messages, list) else []


def _extract_cwd(records: list[dict[str, Any]]) -> str:
    for record in records:
        body = record.get("request", {}).get("body")
        if not isinstance(body, dict):
            continue
        match = _CWD_PATTERN.search(_content_text(body.get("system")))
        if match:
            return match.group(1).strip()
    return ""


def _first_message_text(messages: list[Any]) -> str:
    if not messages:
        return ""
    first = messages[0]
    return _content_text(first.get("content")) if isinstance(first, dict) else ""


def _normalize_whitespace(text: str) -> str:
    return " ".join(text.split())


def _resume_summary_probe(first_text: str) -> str:
    """Return a normalized chunk of the embedded compact summary."""
    marker_index = first_text.find(COMPACT_RESUME_MARKER)
    if marker_index < 0:
        return ""
    tail = first_text[marker_index + len(COMPACT_RESUME_MARKER) :]
    summary_index = tail.find("Summary:")
    if summary_index >= 0:
        tail = tail[summary_index + len("Summary:") :]
    else:
        newline_index = tail.find("\n")
        tail = tail[newline_index + 1 :] if newline_index >= 0 else tail
    return _normalize_whitespace(tail)[:_SUMMARY_PROBE_CHARS]


def _boundary_main_records(
    store: TraceStore, session_id: str, record_count: int
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[dict[str, Any]]]:
    """Return the first and last main-loop records, decoding edge windows first."""
    if record_count <= 2 * _BOUNDARY_WINDOW:
        records = store.load_records(session_id)
    else:
        head = store.load_records(session_id, limit=_BOUNDARY_WINDOW)
        tail = store.load_records(session_id, limit=_BOUNDARY_WINDOW, offset=record_count - _BOUNDARY_WINDOW)
        records = head + tail
        if not any(_is_main_loop_record(record) for record in records):
            records = store.load_records(session_id)
    mains = [record for record in records if _is_main_loop_record(record)]
    if not mains:
        return None, None, records
    return mains[0], mains[-1], records


def compute_session_fingerprint(store: TraceStore, session_id: str, record_count: int) -> dict[str, Any] | None:
    """Summarize the continuation-relevant shape of one stored session."""
    first, last, records = _boundary_main_records(store, session_id, record_count)
    if first is None or last is None:
        return None
    first_conversation = _conversation_messages(_record_messages(first))
    last_conversation = _conversation_messages(_record_messages(last))
    first_text = _first_message_text(first_conversation)
    return {
        "first_digests": [_message_digest(message) for message in first_conversation],
        "last_digests": [_message_digest(message) for message in last_conversation],
        "cwd": _extract_cwd(records),
        "resume_probe": _resume_summary_probe(first_text),
        "last_response": _normalize_whitespace(_record_response_text(last)),
    }


def _cached_fingerprint(store: TraceStore, session_id: str, record_count: int) -> dict[str, Any] | None:
    with _fingerprint_cache_lock:
        cached = _fingerprint_cache.get(session_id)
        if cached is not None and cached[0] == record_count:
            return cached[1]
    fingerprint = compute_session_fingerprint(store, session_id, record_count)
    with _fingerprint_cache_lock:
        _fingerprint_cache[session_id] = (record_count, fingerprint)
    return fingerprint


def continuation_link_type(earlier: dict[str, Any] | None, later: dict[str, Any] | None) -> str | None:
    """Classify how ``later`` continues ``earlier``: 'prefix', 'compact', or None."""
    if not earlier or not later:
        return None
    if earlier.get("cwd") != later.get("cwd"):
        return None
    history = earlier.get("last_digests") or []
    replay = later.get("first_digests") or []
    if history and len(replay) >= max(2, len(history)) and replay[0] == history[0]:
        matched = 0
        for old, new in zip(history, replay):
            if old != new:
                break
            matched += 1
        if matched >= len(history) - _PREFIX_TAIL_TOLERANCE:
            return "prefix"
    probe = later.get("resume_probe") or ""
    response = earlier.get("last_response") or ""
    if len(probe) >= _MIN_SUMMARY_PROBE_CHARS and probe in response:
        return "compact"
    return None


def find_continuation_chains(store: TraceStore, session_rows: list[Any]) -> list[dict[str, Any]]:
    """Group stored sessions into continuation chains.

    ``session_rows`` are sqlite rows (or mappings) with ``id``, ``started_at``,
    ``client``, and ``record_count`` keys. Returns chains of two or more
    sessions ordered by start time; every chain entry after the head carries
    the link type that attaches it to its predecessor.
    """
    sessions = sorted(
        (
            {
                "id": row["id"],
                "started_at": row["started_at"],
                "client": row["client"],
                "record_count": row["record_count"],
            }
            for row in session_rows
        ),
        key=lambda item: str(item["started_at"]),
    )
    fingerprints = {
        session["id"]: _cached_fingerprint(store, session["id"], session["record_count"]) for session in sessions
    }

    predecessor: dict[str, tuple[str, str]] = {}
    successor: dict[str, str] = {}
    for index, later in enumerate(sessions):
        for earlier in reversed(sessions[:index]):
            if earlier["id"] in successor or earlier["client"] != later["client"]:
                continue
            link = continuation_link_type(fingerprints[earlier["id"]], fingerprints[later["id"]])
            if link:
                predecessor[later["id"]] = (earlier["id"], link)
                successor[earlier["id"]] = later["id"]
                break

    chains: list[dict[str, Any]] = []
    for session in sessions:
        session_id = session["id"]
        if session_id in predecessor or session_id not in successor:
            continue
        chain_ids = [session_id]
        links = []
        current = session_id
        while current in successor:
            current = successor[current]
            chain_ids.append(current)
            links.append({"session_id": current, "link": predecessor[current][1]})
        chains.append({"session_ids": chain_ids, "links": links})
    return chains
