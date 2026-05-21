"""Session-first dashboard helpers for trace history browsing."""

from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from claude_tap.usage import normalize_usage

DASHBOARD_TEMPLATE_PATH = Path(__file__).parent / "dashboard.html"
_MANIFEST_FILE = ".cloudtap-manifest.json"
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


def session_id_for_rel_path(rel_path: str) -> str:
    """Encode a relative trace path as a URL-safe session id."""
    return base64.urlsafe_b64encode(rel_path.encode("utf-8")).decode("ascii").rstrip("=")


def rel_path_for_session_id(session_id: str) -> str | None:
    """Decode a URL-safe session id back to a relative trace path."""
    padding = "=" * (-len(session_id) % 4)
    try:
        value = base64.urlsafe_b64decode((session_id + padding).encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    if value.startswith("/") or ".." in Path(value).parts:
        return None
    return value


def list_trace_sessions(output_dir: Path, current_trace_path: Path | None = None) -> list[dict[str, Any]]:
    """Return trace sessions sorted by most recent activity."""
    output_dir = output_dir.resolve()
    manifest = _manifest_by_trace_path(output_dir)
    current_resolved = current_trace_path.resolve() if current_trace_path else None

    sessions: list[dict[str, Any]] = []
    for trace_path in _iter_trace_files(output_dir):
        rel_path = _rel_posix(trace_path, output_dir)
        records = _read_jsonl_records(trace_path)
        sessions.append(
            _summarize_session(
                output_dir=output_dir,
                trace_path=trace_path,
                rel_path=rel_path,
                records=records,
                manifest_entry=manifest.get(rel_path, {}),
                is_current=current_resolved == trace_path.resolve(),
            )
        )

    sessions.sort(key=lambda item: (item.get("updated_at") or "", item.get("trace_path") or ""), reverse=True)
    return sessions


def list_trace_agents(output_dir: Path, current_trace_path: Path | None = None) -> list[dict[str, Any]]:
    """Return agent buckets for the dashboard sidebar."""
    sessions = list_trace_sessions(output_dir, current_trace_path=current_trace_path)
    buckets: dict[str, dict[str, Any]] = {}
    for session in sessions:
        key = session["agent_key"]
        bucket = buckets.setdefault(key, {"key": key, "label": session["agent"], "sessions": 0, "records": 0})
        bucket["sessions"] += 1
        bucket["records"] += session["record_count"]
    return sorted(buckets.values(), key=lambda item: (item["label"].lower(), item["key"]))


def load_trace_session(
    output_dir: Path,
    session_id: str,
    current_trace_path: Path | None = None,
) -> dict[str, Any] | None:
    """Load one session summary and its records by session id."""
    trace_path = trace_path_for_session_id(output_dir, session_id)
    if trace_path is None:
        return None

    output_dir = output_dir.resolve()
    rel_path = _rel_posix(trace_path, output_dir)
    manifest = _manifest_by_trace_path(output_dir)
    records = _read_jsonl_records(trace_path)
    current_resolved = current_trace_path.resolve() if current_trace_path else None
    summary = _summarize_session(
        output_dir=output_dir,
        trace_path=trace_path,
        rel_path=rel_path,
        records=records,
        manifest_entry=manifest.get(rel_path, {}),
        is_current=current_resolved == trace_path.resolve(),
    )
    return {"session": summary, "records": records}


def trace_path_for_session_id(output_dir: Path, session_id: str) -> Path | None:
    """Resolve a session id to a trace path inside the output directory."""
    rel_path = rel_path_for_session_id(session_id)
    if rel_path is None:
        return None
    output_dir = output_dir.resolve()
    trace_path = (output_dir / rel_path).resolve()
    try:
        trace_path.relative_to(output_dir)
    except ValueError:
        return None
    if not trace_path.is_file() or trace_path.suffix != ".jsonl":
        return None
    return trace_path


def dashboard_trace_snapshot(output_dir: Path) -> dict[str, tuple[int, int]]:
    """Return a cheap file snapshot for dashboard refresh detection."""
    if not output_dir.is_dir():
        return {}
    snapshot: dict[str, tuple[int, int]] = {}
    for trace_path in _iter_trace_files(output_dir.resolve()):
        try:
            stat = trace_path.stat()
        except OSError:
            continue
        snapshot[_rel_posix(trace_path, output_dir.resolve())] = (stat.st_mtime_ns, stat.st_size)
    return snapshot


def _iter_trace_files(output_dir: Path) -> list[Path]:
    if not output_dir.is_dir():
        return []
    return sorted(path for path in output_dir.glob("**/trace_*.jsonl") if path.is_file())


def _rel_posix(path: Path, base: Path) -> str:
    return path.relative_to(base).as_posix()


def _read_jsonl_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return records
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _manifest_by_trace_path(output_dir: Path) -> dict[str, dict[str, Any]]:
    manifest_path = output_dir / _MANIFEST_FILE
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(manifest, dict):
        return {}

    entries: dict[str, dict[str, Any]] = {}
    for entry in manifest.get("traces", []):
        if not isinstance(entry, dict):
            continue
        for file_name in entry.get("files", []):
            if not isinstance(file_name, str):
                continue
            rel = file_name.replace("\\", "/")
            if rel.endswith(".jsonl"):
                entries[rel] = entry
    return entries


def _summarize_session(
    *,
    output_dir: Path,
    trace_path: Path,
    rel_path: str,
    records: list[dict[str, Any]],
    manifest_entry: dict[str, Any],
    is_current: bool,
) -> dict[str, Any]:
    stat = trace_path.stat()
    html_path = trace_path.with_suffix(".html")
    log_path = trace_path.with_suffix(".log")
    first_record = records[0] if records else {}
    last_record = records[-1] if records else {}
    started_at = (
        _timestamp_from_record(first_record) or _manifest_time(manifest_entry) or _iso_from_timestamp(stat.st_mtime)
    )
    updated_at = _timestamp_from_record(last_record) or _iso_from_timestamp(stat.st_mtime)
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
        status = _response_status(record)
        if status:
            statuses.append(status)
        duration_ms += _duration_ms(record)
        turn = record.get("turn")
        if isinstance(turn, int):
            turns.add(turn)

    has_error = any(status >= 400 for status in statuses) or any(_record_error(record) for record in records)
    status = "error" if has_error else "active" if is_current or (not html_path.exists() and records) else "complete"
    if not records:
        status = "empty"

    preview_records = _preview_records(records)
    return {
        "id": session_id_for_rel_path(rel_path),
        "date": trace_path.parent.name if _DATE_RE.match(trace_path.parent.name) else "legacy",
        "agent": agent,
        "agent_key": _agent_key(agent),
        "status": status,
        "live": is_current,
        "trace_path": str(trace_path),
        "rel_trace_path": rel_path,
        "html_path": str(html_path) if html_path.exists() else None,
        "log_path": str(log_path) if log_path.exists() else None,
        "started_at": started_at,
        "updated_at": updated_at,
        "record_count": len(records),
        "turn_count": len(turns) if turns else len(records),
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
        "size_bytes": stat.st_size,
    }


def _timestamp_from_record(record: dict[str, Any]) -> str | None:
    value = record.get("timestamp")
    return value if isinstance(value, str) and value else None


def _manifest_time(entry: dict[str, Any]) -> str | None:
    value = entry.get("created_at")
    return value if isinstance(value, str) and value else None


def _iso_from_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


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
