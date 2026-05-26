"""Session-first dashboard helpers backed by the local SQLite trace store."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from claude_tap.trace_store import TraceStore, get_trace_store
from claude_tap.usage import normalize_usage

DASHBOARD_TEMPLATE_PATH = Path(__file__).parent / "dashboard.html"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

CLIENT_LABELS = {
    "agy": "Antigravity",
    "antigravity": "Antigravity",
    "claude": "Claude Code",
    "codex": "Codex",
    "cursor": "Cursor",
    "gemini": "Gemini",
    "hermes": "Hermes",
    "kimi": "Kimi",
    "opencode": "OpenCode",
    "pi": "Pi",
    "qoder": "Qoder",
}


def read_dashboard_template() -> str:
    """Read the packaged dashboard HTML."""
    return DASHBOARD_TEMPLATE_PATH.read_text(encoding="utf-8")


def ensure_trace_store() -> TraceStore:
    """Return the trace store."""
    return get_trace_store()


def list_trace_sessions(
    current_session_id: str | None = None,
    *,
    live_record_count: int | None = None,
) -> list[dict[str, Any]]:
    """Return trace sessions sorted by most recent activity."""
    store = ensure_trace_store()
    try:
        sessions = [
            _apply_current_session_state(
                _session_summary_from_row(store, row),
                current_session_id,
                live_record_count=(
                    live_record_count
                    if live_record_count is not None and current_session_id and row["id"] == current_session_id
                    else None
                ),
            )
            for row in store.list_session_rows()
        ]
    except (OSError, sqlite3.Error, json.JSONDecodeError, ValueError):
        return []
    sessions.sort(key=lambda item: (_timestamp_sort_value(item.get("updated_at")), item.get("id") or ""), reverse=True)
    return sessions


def list_trace_agents(
    current_session_id: str | None = None,
    *,
    live_record_count: int | None = None,
) -> list[dict[str, Any]]:
    """Return agent buckets for the dashboard sidebar."""
    sessions = list_trace_sessions(current_session_id, live_record_count=live_record_count)
    buckets: dict[str, dict[str, Any]] = {}
    for session in sessions:
        key = session["agent_key"]
        bucket = buckets.setdefault(key, {"key": key, "label": session["agent"], "sessions": 0, "records": 0})
        bucket["sessions"] += 1
        bucket["records"] += session["record_count"]
    return sorted(buckets.values(), key=lambda item: (item["label"].lower(), item["key"]))


def dashboard_trace_snapshot() -> dict[str, tuple[str, int, str]]:
    """Return a cheap SQLite snapshot for dashboard refresh detection."""
    store = ensure_trace_store()
    return store.dashboard_snapshot()


def load_trace_session(
    session_id: str,
    current_session_id: str | None = None,
    record_limit: int | None = None,
    record_offset: int = 0,
    *,
    live_record_count: int | None = None,
) -> dict[str, Any] | None:
    """Load one session summary and its records by session id."""
    store = ensure_trace_store()
    row = store.load_session_row(session_id)
    if row is None:
        return None
    summary = _apply_current_session_state(
        _session_summary_from_row(store, row),
        current_session_id,
        live_record_count=(
            live_record_count
            if live_record_count is not None and current_session_id and row["id"] == current_session_id
            else None
        ),
    )
    records = store.load_records(session_id, limit=record_limit, offset=record_offset)
    return {"session": summary, "records": records}


def merge_record_into_summary(
    summary: dict[str, Any] | None,
    *,
    row: sqlite3.Row,
    record: dict[str, Any],
    record_count: int,
) -> dict[str, Any]:
    """Update a session summary incrementally after appending one record."""
    manifest_entry = {
        "client": row["client"] or "",
        "proxy_mode": row["proxy_mode"] or "",
    }
    if summary is None or summary.get("id") != row["id"]:
        return _summarize_session(
            session_id=row["id"],
            date_key=row["date_key"] or "legacy",
            legacy_rel_path=row["legacy_rel_path"],
            records=[record],
            manifest_entry=manifest_entry,
            status="active",
            started_at=row["started_at"] or "",
            updated_at=row["updated_at"] or "",
            is_current=True,
            record_count=record_count,
        )

    summary = dict(summary)
    usage = _record_usage(record)
    summary["record_count"] = record_count
    summary["turn_count"] = max(int(summary.get("turn_count") or 0), record_count)
    summary["input_tokens"] = int(summary.get("input_tokens") or 0) + usage.get("input_tokens", 0)
    summary["output_tokens"] = int(summary.get("output_tokens") or 0) + usage.get("output_tokens", 0)
    summary["cache_read_tokens"] = int(summary.get("cache_read_tokens") or 0) + usage.get("cache_read_input_tokens", 0)
    summary["cache_create_tokens"] = int(summary.get("cache_create_tokens") or 0) + usage.get(
        "cache_creation_input_tokens", 0
    )
    summary["total_tokens"] = (
        summary["input_tokens"]
        + summary["output_tokens"]
        + summary["cache_read_tokens"]
        + summary["cache_create_tokens"]
    )
    summary["duration_ms"] = int(summary.get("duration_ms") or 0) + _duration_ms(record)
    model = _record_model(record)
    if model:
        summary["model"] = model
    timestamp = _timestamp_from_record(record)
    if timestamp:
        summary["updated_at"] = timestamp
        if not summary.get("started_at"):
            summary["started_at"] = timestamp
    summary["last_response"] = _last_response_preview([record])
    if not summary.get("first_user"):
        summary["first_user"] = _first_user_preview([record])
    if not summary.get("agent"):
        summary["agent"] = _infer_agent([record], manifest_entry)
        summary["agent_key"] = _agent_key(summary["agent"])
    status_code = _response_status(record)
    if status_code >= 400 or _record_error(record):
        summary["status"] = "error"
        summary["error"] = summary.get("error") or _first_error([record])
    else:
        summary["status"] = "active"
    return summary


def build_imported_session_summary(
    row: sqlite3.Row,
    records: list[dict[str, Any]],
    manifest_entry: dict[str, Any],
) -> dict[str, Any]:
    """Build and cache a summary for a legacy import."""
    return _summarize_session(
        session_id=row["id"],
        date_key=row["date_key"] or "legacy",
        legacy_rel_path=row["legacy_rel_path"],
        records=records,
        manifest_entry=manifest_entry,
        status="complete",
        started_at=row["started_at"] or "",
        updated_at=row["updated_at"] or "",
        is_current=False,
        record_count=int(row["record_count"] or len(records)),
    )


def _session_summary_from_row(store: TraceStore, row: sqlite3.Row) -> dict[str, Any]:
    summary_json = row["summary_json"]
    if summary_json:
        try:
            cached = json.loads(summary_json)
        except json.JSONDecodeError:
            cached = None
        if isinstance(cached, dict) and cached.get("id") == row["id"]:
            if row["status"] != "active":
                return cached
            cached = dict(cached)
            db_count = int(row["record_count"] or 0)
            cached["updated_at"] = row["updated_at"] or cached.get("updated_at") or ""
            cached["record_count"] = db_count
            cached["turn_count"] = max(int(cached.get("turn_count") or 0), db_count)
            if db_count > 0 and cached.get("status") != "error":
                cached["status"] = "active"
            return cached

    record_count = int(row["record_count"] or 0)
    manifest_entry = {
        "client": row["client"] or "",
        "proxy_mode": row["proxy_mode"] or "",
    }
    if record_count == 0:
        summary = _summarize_session(
            session_id=row["id"],
            date_key=row["date_key"] or "legacy",
            legacy_rel_path=row["legacy_rel_path"],
            records=[],
            manifest_entry=manifest_entry,
            status=row["status"] or "empty",
            started_at=row["started_at"] or "",
            updated_at=row["updated_at"] or "",
            is_current=row["status"] == "active",
            record_count=0,
        )
        if row["status"] != "active":
            store.store_summary(row["id"], summary)
        return summary

    records = store.load_records(row["id"])
    summary = _summarize_session(
        session_id=row["id"],
        date_key=row["date_key"] or "legacy",
        legacy_rel_path=row["legacy_rel_path"],
        records=records,
        manifest_entry=manifest_entry,
        status=row["status"] or "complete",
        started_at=row["started_at"] or "",
        updated_at=row["updated_at"] or "",
        is_current=False,
        record_count=record_count,
    )
    if row["status"] != "active":
        store.store_summary(row["id"], summary)
    return summary


def _apply_current_session_state(
    session: dict[str, Any],
    current_session_id: str | None,
    *,
    live_record_count: int | None = None,
) -> dict[str, Any]:
    session = dict(session)
    is_current = bool(current_session_id and session.get("id") == current_session_id)
    session["live"] = is_current
    if is_current:
        count = int(session.get("record_count") or 0)
        if live_record_count is not None:
            count = max(count, live_record_count)
            session["record_count"] = count
            session["turn_count"] = max(int(session.get("turn_count") or 0), count)
        if count > 0 and session.get("status") != "error":
            session["status"] = "active"
    return session


def _summarize_session(
    *,
    session_id: str,
    date_key: str,
    legacy_rel_path: str | None,
    records: list[dict[str, Any]],
    manifest_entry: dict[str, Any],
    status: str,
    started_at: str,
    updated_at: str,
    is_current: bool,
    record_count: int | None = None,
) -> dict[str, Any]:
    first_record = records[0] if records else {}
    last_record = records[-1] if records else {}
    started_at = _timestamp_from_record(first_record) or started_at or _iso_now()
    updated_at = _timestamp_from_record(last_record) or updated_at or started_at
    agent = _infer_agent(records, manifest_entry)
    input_tokens = output_tokens = cache_read_tokens = cache_create_tokens = 0
    models: dict[str, int] = {}
    statuses: list[int] = []
    duration_ms = 0
    turns: set[int] = set()

    for record in records:
        usage = _record_usage(record)
        input_tokens += usage.get("input_tokens", 0)
        output_tokens += usage.get("output_tokens", 0)
        cache_read_tokens += usage.get("cache_read_input_tokens", 0)
        cache_create_tokens += usage.get("cache_creation_input_tokens", 0)
        model = _record_model(record)
        if model:
            models[model] = models.get(model, 0) + 1
        status_code = _response_status(record)
        if status_code:
            statuses.append(status_code)
        duration_ms += _duration_ms(record)
        turn = record.get("turn")
        if isinstance(turn, int):
            turns.add(turn)

    has_error = any(code >= 400 for code in statuses) or any(_record_error(record) for record in records)
    if has_error:
        resolved_status = "error"
    elif is_current and records:
        resolved_status = "active"
    elif not records:
        resolved_status = "empty"
    else:
        resolved_status = status if status in {"active", "complete", "error", "empty"} else "complete"

    preview_records = _preview_records(records)
    count = record_count if record_count is not None else len(records)
    return {
        "id": session_id,
        "date": date_key if _DATE_RE.match(date_key) else "legacy",
        "agent": agent,
        "agent_key": _agent_key(agent),
        "status": resolved_status,
        "live": is_current,
        "legacy_rel_path": legacy_rel_path,
        "started_at": started_at,
        "updated_at": updated_at,
        "record_count": count,
        "turn_count": len(turns) if turns else count,
        "duration_ms": duration_ms,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_create_tokens": cache_create_tokens,
        "total_tokens": input_tokens + output_tokens + cache_read_tokens + cache_create_tokens,
        "model": _top_key(models) or _record_model(last_record) or "unknown",
        "first_user": _first_user_preview(preview_records),
        "last_response": _last_response_preview(preview_records),
        "error": _first_error(records),
    }


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp_sort_value(value: object) -> float:
    if not isinstance(value, str) or not value:
        return 0.0
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).timestamp()


def _timestamp_from_record(record: dict[str, Any]) -> str | None:
    value = record.get("timestamp")
    return value if isinstance(value, str) and value else None


def _record_usage(record: dict[str, Any]) -> dict[str, int]:
    response = record.get("response")
    body = response.get("body") if isinstance(response, dict) else {}
    usage = body.get("usage", {}) if isinstance(body, dict) else {}
    if not usage and isinstance(body, dict):
        usage = body.get("usageMetadata", {})
    if not usage:
        for event in reversed(_response_events(record)):
            payload = _event_payload(event)
            candidate = payload.get("usage", {}) if isinstance(payload, dict) else {}
            if candidate:
                usage = candidate
                break
    if not usage and isinstance(body, dict):
        usage = body
    return normalize_usage(usage)


def _record_model(record: dict[str, Any]) -> str:
    request = record.get("request")
    req_body = request.get("body") if isinstance(request, dict) else None
    if isinstance(req_body, dict):
        for key in ("model", "modelId"):
            value = req_body.get(key)
            if isinstance(value, str) and value:
                return value
        nested_request = req_body.get("request")
        if isinstance(nested_request, dict):
            value = nested_request.get("model")
            if isinstance(value, str) and value:
                return value
    response = record.get("response")
    resp_body = response.get("body") if isinstance(response, dict) else None
    if isinstance(resp_body, dict):
        value = resp_body.get("model")
        if isinstance(value, str) and value:
            return value
    path = request.get("path") if isinstance(request, dict) else ""
    if isinstance(path, str):
        match = re.search(r"/models/([^:?/]+)", path)
        if match:
            return match.group(1)
    return ""


def _response_status(record: dict[str, Any]) -> int:
    response = record.get("response")
    status = response.get("status") if isinstance(response, dict) else None
    return status if isinstance(status, int) else 0


def _duration_ms(record: dict[str, Any]) -> int:
    value = record.get("duration_ms")
    return value if isinstance(value, int) else 0


def _record_error(record: dict[str, Any]) -> str:
    response = record.get("response")
    if not isinstance(response, dict):
        return ""
    value = response.get("error")
    return value if isinstance(value, str) else ""


def _first_error(records: list[dict[str, Any]]) -> str:
    for record in records:
        error = _record_error(record)
        if error:
            return _preview(error, 240)
        response = record.get("response")
        body = response.get("body") if isinstance(response, dict) else None
        if isinstance(body, dict):
            value = body.get("error")
            if isinstance(value, str):
                return _preview(value, 240)
            if isinstance(value, dict):
                message = value.get("message")
                if isinstance(message, str):
                    return _preview(message, 240)
    return ""


def _top_key(values: dict[str, int]) -> str:
    if not values:
        return ""
    return max(values.items(), key=lambda item: item[1])[0]


def _infer_agent(records: list[dict[str, Any]], manifest_entry: dict[str, Any]) -> str:
    client = manifest_entry.get("client")
    if not client and isinstance(manifest_entry.get("metadata"), dict):
        client = manifest_entry["metadata"].get("client")
    if isinstance(client, str) and client:
        return CLIENT_LABELS.get(client.lower(), client)

    for record in records:
        capture = record.get("capture")
        if isinstance(capture, dict):
            record_client = capture.get("client")
            if isinstance(record_client, str) and record_client:
                return CLIENT_LABELS.get(record_client.lower(), record_client)

    sample = records[0] if records else {}
    host = _record_host(sample)
    path = _record_path(sample)
    upstream = str(sample.get("upstream_base_url") or "")
    signal = " ".join([host, path, upstream]).lower()
    if "antigravity" in signal or "codeium" in signal or "v1internal:streamgeneratecontent" in signal:
        return "Antigravity"
    if (
        "generativelanguage.googleapis.com" in signal
        or "streamgeneratecontent" in signal
        or "generatecontent" in signal
    ):
        return "Gemini"
    if "chatgpt.com/backend-api/codex" in signal or "/responses" in signal:
        return "Codex"
    if "api.anthropic.com" in signal or "/v1/messages" in signal:
        return "Claude Code"
    if "kimi" in signal or "moonshot" in signal:
        return "Kimi"
    if "cursor" in signal:
        return "Cursor"
    if "qoder" in signal:
        return "Qoder"
    if "opencode" in signal:
        return "OpenCode"
    if "hermes" in signal:
        return "Hermes"
    return "Unknown"


def _record_host(record: dict[str, Any]) -> str:
    request = record.get("request")
    headers = request.get("headers") if isinstance(request, dict) else {}
    if isinstance(headers, dict):
        for key in ("Host", "host"):
            value = headers.get(key)
            if isinstance(value, str):
                return value
    upstream = record.get("upstream_base_url")
    if isinstance(upstream, str) and upstream:
        return urlparse(upstream).netloc
    return ""


def _record_path(record: dict[str, Any]) -> str:
    request = record.get("request")
    value = request.get("path") if isinstance(request, dict) else ""
    return value if isinstance(value, str) else ""


def _agent_key(agent: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "-", agent.lower()).strip("-")
    return key or "unknown"


def _preview_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    primary = [record for record in records if _is_primary_model_record(record)]
    if primary:
        return primary
    return [record for record in records if not _is_auxiliary_record(record)]


def _is_primary_model_record(record: dict[str, Any]) -> bool:
    path = _record_path(record).lower()
    if not path:
        return False
    primary_fragments = (
        "/v1/messages",
        "/zen/v1/messages",
        "/v1/responses",
        "/responses",
        "/v1/chat/completions",
        "/chat/completions",
        "/v1/completions",
        "/completions",
        "streamgeneratecontent",
        "generatecontent",
    )
    return any(fragment in path for fragment in primary_fragments)


def _is_auxiliary_record(record: dict[str, Any]) -> bool:
    path = _record_path(record).lower()
    auxiliary_fragments = (
        "/token",
        "oauth",
        "userinfo",
        "quota",
        "experiments",
        "admincontrols",
        "features",
        "register",
        "manifest",
        "/metrics",
        "/log",
        "loadcodeassist",
        "fetchavailablemodels",
        "fetchuserinfo",
    )
    return any(fragment in path for fragment in auxiliary_fragments)


def _first_user_preview(records: list[dict[str, Any]]) -> str:
    for record in records:
        request = record.get("request")
        body = request.get("body") if isinstance(request, dict) else None
        text = _request_user_text(body)
        if text:
            return _preview(text, 220)
    return ""


def _last_response_preview(records: list[dict[str, Any]]) -> str:
    for record in reversed(records):
        text = _record_response_text(record)
        if text:
            return _preview(text, 220)
    return ""


def _record_response_text(record: dict[str, Any]) -> str:
    response = record.get("response")
    body = response.get("body") if isinstance(response, dict) else None
    text = _response_text(body)
    if text:
        return text

    for event in reversed(_response_events(record)):
        payload = _event_payload(event)
        text = _response_text(payload)
        if text:
            return text
        if isinstance(event, dict):
            text = _content_text(event.get("item")) or _content_text(event.get("part"))
            if text:
                return text
            value = event.get("text")
            if isinstance(value, str) and value:
                return value
    return ""


def _response_events(record: dict[str, Any]) -> list[dict[str, Any]]:
    response = record.get("response")
    if not isinstance(response, dict):
        return []
    events = response.get("sse_events")
    if isinstance(events, list) and events:
        return [event for event in events if isinstance(event, dict)]
    events = response.get("ws_events")
    if isinstance(events, list):
        return [event for event in events if isinstance(event, dict)]
    return []


def _event_payload(event: dict[str, Any]) -> dict[str, Any]:
    data = event.get("data", event)
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return {}
    if isinstance(data, dict):
        response = data.get("response")
        if isinstance(response, dict):
            return response
        return data
    return {}


def _request_user_text(body: Any) -> str:
    if isinstance(body, str):
        return body
    if not isinstance(body, dict):
        return ""

    messages = body.get("messages")
    if isinstance(messages, list):
        for message in messages:
            role = str(message.get("role") or "").lower() if isinstance(message, dict) else ""
            if isinstance(message, dict) and role == "user":
                text = _content_text(message.get("content"))
                prompt = _clean_user_prompt_text(text)
                if prompt:
                    return prompt

    text = _input_user_text(body.get("input"))
    if text:
        return text

    request = body.get("request")
    if isinstance(request, dict):
        contents = request.get("contents")
    else:
        contents = body.get("contents")
    if isinstance(contents, list):
        for content in contents:
            if not isinstance(content, dict):
                continue
            role = str(content.get("role") or "user").lower()
            if role != "user":
                continue
            text = _parts_text(content.get("parts"))
            prompt = _clean_user_prompt_text(text)
            if prompt:
                return prompt

    prompt = body.get("prompt")
    return _clean_user_prompt_text(prompt) if isinstance(prompt, str) else ""


def _input_user_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        role = str(value.get("role") or "").lower()
        if role == "user":
            return _clean_user_prompt_text(_content_text(value.get("content") or value.get("text")))
        return ""
    if not isinstance(value, list):
        return ""

    for item in value:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").lower()
        if role == "user":
            text = _content_text(item.get("content") or item.get("text"))
            prompt = _clean_user_prompt_text(text)
            if prompt:
                return prompt

    for item in value:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").lower()
        item_type = str(item.get("type") or "").lower()
        if role or item_type in ("function_call_output", "tool_result", "reasoning"):
            continue
        if item_type in ("message", "input_text") or "content" in item:
            text = _content_text(item.get("content") or item.get("text"))
            prompt = _clean_user_prompt_text(text)
            if prompt:
                return prompt
    return ""


def _clean_user_prompt_text(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    if len(text) >= 2 and text[0] == text[-1] == '"':
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, str) and decoded:
            text = decoded.strip()

    request = re.search(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", text, flags=re.DOTALL | re.IGNORECASE)
    if request:
        return request.group(1).strip()

    session = re.fullmatch(r"<session>\s*(.*?)\s*</session>", text, flags=re.DOTALL | re.IGNORECASE)
    if session:
        return session.group(1).strip()

    first_tag = re.match(r"^<([A-Za-z_-]+)>", text)
    if first_tag and first_tag.group(1).lower() in {
        "artifacts",
        "environment_context",
        "session_context",
        "skills",
        "slash_commands",
        "subagents",
        "system-reminder",
        "user_information",
    }:
        return ""

    if text.startswith("# AGENTS.md instructions") or text.startswith("<INSTRUCTIONS>"):
        return ""

    return text


def _response_text(body: Any) -> str:
    if isinstance(body, str):
        return body
    if not isinstance(body, dict):
        return ""

    text = _content_text(body.get("content"))
    if text:
        return text

    candidates = body.get("candidates")
    if isinstance(candidates, list):
        texts = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            content = candidate.get("content")
            if isinstance(content, dict):
                texts.append(_parts_text(content.get("parts")))
        text = "\n".join(part for part in texts if part).strip()
        if text:
            return text

    choices = body.get("choices")
    if isinstance(choices, list):
        texts = []
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message") or choice.get("delta")
            if isinstance(message, dict):
                texts.append(_content_text(message.get("content")))
        text = "\n".join(part for part in texts if part).strip()
        if text:
            return text

    output = body.get("output")
    text = _content_text(output)
    if text:
        return text

    value = body.get("response")
    return _content_text(value)


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                for key in ("text", "content", "output_text", "input_text"):
                    text = item.get(key)
                    if isinstance(text, str):
                        parts.append(text)
                        break
                    if isinstance(text, list):
                        parts.append(_content_text(text))
                        break
                else:
                    if item.get("type") in ("message", "assistant"):
                        parts.append(_content_text(item.get("content")))
        return "\n".join(part for part in parts if part).strip()
    if isinstance(value, dict):
        for key in ("text", "content", "output_text", "input_text"):
            text = value.get(key)
            if isinstance(text, (str, list, dict)):
                return _content_text(text)
    return ""


def _parts_text(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    parts = []
    for item in value:
        if isinstance(item, dict):
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts).strip()


def _preview(value: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."
