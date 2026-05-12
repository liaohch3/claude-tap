"""HTML viewer generation – embed JSONL data into a self-contained HTML file."""

from __future__ import annotations

import base64
import html as html_lib
import json
from datetime import datetime
from importlib.metadata import version as _pkg_version
from pathlib import Path
from urllib.parse import urlparse

from claude_tap.sse import SSEReassembler
from claude_tap.usage import normalize_usage

try:
    CLAUDE_TAP_VERSION = _pkg_version("claude-tap")
except Exception:
    CLAUDE_TAP_VERSION = "0.0.0"

# Threshold: traces with more entries than this use lazy mode
LAZY_THRESHOLD = 50
PRIMARY_PATH_PREFIXES = (
    "/v1/messages",
    "/v1/responses",
    "/backend-api/codex/responses",
    "/v1/chat/completions",
    "/v1/completions",
)
SECONDARY_PATH_PREFIXES = (
    "/v1/mcp",
    "/v1/models",
    "/v1/embeddings",
    "/v1/files",
    "/responses",
    "/models",
    "/chat/completions",
    "/completions",
    "/files",
    "/search",
    "/fetch",
    "/usages",
    "/feedback",
)


def _iter_response_events(resp: dict) -> list[dict]:
    """Return stream events from SSE or WebSocket traces."""
    if not isinstance(resp, dict):
        return []
    events = resp.get("sse_events")
    if isinstance(events, list) and events:
        return events
    events = resp.get("ws_events")
    if isinstance(events, list):
        return events
    return []


def _event_type(event: dict) -> str:
    if not isinstance(event, dict):
        return ""
    value = event.get("event") or event.get("type")
    return value if isinstance(value, str) else ""


def _event_payload(event: dict) -> dict | None:
    if not isinstance(event, dict):
        return None
    payload = event.get("data", event)
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            return None
    return payload if isinstance(payload, dict) else None


def _decode_bedrock_eventstream_events(body: object) -> list[dict]:
    """Extract Anthropic stream events from a decoded AWS EventStream body.

    Bedrock invoke-with-response-stream responses are binary AWS EventStream
    frames. Legacy traces may contain those bytes decoded as text with invalid
    frame bytes replaced, but the JSON payloads inside the frames remain intact.
    """
    if not isinstance(body, str) or '"bytes"' not in body:
        return []

    events: list[dict] = []
    decoder = json.JSONDecoder()
    pos = 0
    while True:
        start = body.find('{"', pos)
        if start < 0:
            break
        try:
            frame, end = decoder.raw_decode(body[start:])
        except json.JSONDecodeError:
            pos = start + 1
            continue
        pos = start + end

        if not isinstance(frame, dict):
            continue
        encoded = frame.get("bytes")
        if not isinstance(encoded, str):
            continue
        try:
            payload_bytes = base64.b64decode(encoded, validate=True)
            payload = json.loads(payload_bytes)
        except (ValueError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue

        event_type = payload.get("type")
        if isinstance(event_type, str) and event_type:
            events.append({"event": event_type, "data": payload})

    return events


def _normalize_record_for_viewer(record_json: str) -> str:
    """Normalize trace variants into the shape expected by viewer.html."""
    try:
        record = json.loads(record_json)
    except (json.JSONDecodeError, TypeError):
        return record_json
    if not isinstance(record, dict):
        return record_json

    response = record.get("response")
    if not isinstance(response, dict):
        return record_json

    events = _decode_bedrock_eventstream_events(response.get("body"))
    if not events:
        return record_json

    reassembler = SSEReassembler()
    for event in events:
        reassembler.add_event(event["event"], event["data"])

    reconstructed = reassembler.reconstruct()
    if reconstructed:
        response["body"] = reconstructed
    response.setdefault("sse_events", events)

    return json.dumps(record, ensure_ascii=False, separators=(",", ":"))


def _parse_function_call_arguments(arguments: object) -> object:
    if isinstance(arguments, str):
        try:
            return json.loads(arguments)
        except (json.JSONDecodeError, TypeError):
            return arguments
    if arguments is None:
        return {}
    return arguments


def _tool_search_output_content(item: dict) -> str:
    names: list[str] = []
    tools = item.get("tools")
    if isinstance(tools, list):
        for namespace in tools:
            if not isinstance(namespace, dict):
                continue
            namespace_name = namespace.get("name")
            if isinstance(namespace_name, str) and namespace_name:
                names.append(namespace_name)
            nested_tools = namespace.get("tools")
            if isinstance(nested_tools, list):
                for tool in nested_tools:
                    if not isinstance(tool, dict):
                        continue
                    tool_name = tool.get("name")
                    if isinstance(tool_name, str) and tool_name:
                        if isinstance(namespace_name, str) and namespace_name:
                            names.append(f"{namespace_name}.{tool_name}")
                        else:
                            names.append(tool_name)
    if names:
        return "tool_search_output\n" + "\n".join(names)
    if isinstance(tools, list):
        return json.dumps(tools, ensure_ascii=False)
    return json.dumps(item, ensure_ascii=False)


def _response_call_tool_name(item: dict) -> str:
    item_type = item.get("type")
    if item_type == "tool_search_call":
        return "tool_search"
    item_name = item.get("name")
    if isinstance(item_name, str) and item_name:
        return item_name
    if isinstance(item_type, str) and item_type.endswith("_call"):
        return item_type[: -len("_call")]
    return ""


def _is_response_call_item(item: dict) -> bool:
    item_type = item.get("type")
    return isinstance(item_type, str) and item_type.endswith("_call")


def _response_call_input(item: dict) -> object:
    if "arguments" in item:
        return _parse_function_call_arguments(item.get("arguments"))
    return {
        key: value for key, value in item.items() if key not in {"id", "type", "status", "call_id", "name", "execution"}
    }


def _is_response_tool_result_item(item: dict) -> bool:
    item_type = item.get("type")
    return item_type == "tool_search_output" or (isinstance(item_type, str) and item_type.endswith("_call_output"))


def _response_tool_result_content(item: dict) -> str:
    if item.get("type") == "tool_search_output":
        return _tool_search_output_content(item)
    if "output" in item:
        output = item.get("output")
        if isinstance(output, str):
            return output
        return json.dumps(output, ensure_ascii=False)
    return json.dumps(
        {key: value for key, value in item.items() if key not in {"id", "type", "status", "call_id", "execution"}},
        ensure_ascii=False,
    )


def _extract_request_messages(body: dict) -> list[dict]:
    if not isinstance(body, dict):
        return []
    msgs = body.get("messages")
    if isinstance(msgs, list) and msgs:
        return [msg for msg in msgs if isinstance(msg, dict)]

    inp = body.get("input")
    if not isinstance(inp, list):
        return []

    normalized = []
    for item in inp:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if _is_response_call_item(item):
            normalized.append(
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": _response_call_tool_name(item),
                            "input": _response_call_input(item),
                        }
                    ],
                }
            )
            continue
        if _is_response_tool_result_item(item):
            normalized.append({"role": "tool", "content": _response_tool_result_content(item)})
            continue
        if item_type not in (None, "message") and "role" not in item:
            continue
        role = item.get("role")
        if not isinstance(role, str) or not role:
            continue
        normalized.append({"role": role, "content": item.get("content")})
    return normalized


def _extract_response_tool_names(output: list) -> list[str]:
    names: list[str] = []
    if not isinstance(output, list):
        return names
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            for c in item.get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    names.append(c.get("name", ""))
        elif _is_response_call_item(item):
            names.append(_response_call_tool_name(item))
    return names


def _extract_response_tool_names_from_output_item_events(events: list[dict]) -> list[str]:
    names: list[str] = []
    for ev in events:
        if _event_type(ev) != "response.output_item.done":
            continue
        data = _event_payload(ev)
        if not isinstance(data, dict):
            continue
        item = data.get("item")
        if isinstance(item, dict):
            names.extend(_extract_response_tool_names([item]))
    return names


def _dict_or_empty(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _tool_display_name(tool: dict) -> str:
    for value in (
        tool.get("name"),
        (tool.get("function") or {}).get("name") if isinstance(tool.get("function"), dict) else None,
        tool.get("id"),
        tool.get("type"),
    ):
        if isinstance(value, str) and value:
            return value
    return ""


def _extract_metadata(record_json: str) -> dict | None:
    """Extract sidebar-relevant metadata from a raw JSON record string.

    Returns a lightweight dict with only the fields needed for sidebar
    rendering, filtering, and search — avoiding full parse of large records.
    """
    try:
        r = json.loads(record_json)
    except (json.JSONDecodeError, TypeError):
        return None

    req = _dict_or_empty(r.get("request"))
    body = _dict_or_empty(req.get("body"))
    resp = _dict_or_empty(r.get("response"))
    resp_body = _dict_or_empty(resp.get("body"))
    stream_events = _iter_response_events(resp)

    # Token usage — from response.body.usage or terminal stream event
    usage = resp_body.get("usage") or {}
    if not usage:
        for ev in reversed(stream_events):
            if _event_type(ev) != "response.completed":
                continue
            data = _event_payload(ev)
            if isinstance(data, dict):
                usage = (data.get("response") or {}).get("usage") or {}
                if usage:
                    break
    usage = normalize_usage(usage)

    # System prompt hint (first 200 chars)
    sys_text = ""
    if isinstance(body.get("system"), str):
        sys_text = body["system"]
    elif isinstance(body.get("system"), list):
        parts = []
        for s in body["system"]:
            if isinstance(s, str):
                parts.append(s)
            elif isinstance(s, dict):
                parts.append(s.get("text", ""))
        sys_text = "\n".join(parts)
    elif isinstance(body.get("instructions"), str):
        sys_text = body["instructions"]

    # Messages
    msgs = _extract_request_messages(body)

    # Tool names from request
    tools = body.get("tools") or []
    tool_names = [_tool_display_name(t) for t in tools if isinstance(t, dict)]

    # Response tool names (tool_use blocks in response content)
    response_tool_names = []
    # Try response.body.content first
    rc = resp_body.get("content") or []
    if rc:
        for block in rc:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                response_tool_names.append(block.get("name", ""))
    else:
        response_tool_names.extend(_extract_response_tool_names(resp_body.get("output") or []))
    if not response_tool_names:
        response_tool_names.extend(_extract_response_tool_names_from_output_item_events(stream_events))
    if not response_tool_names:
        for ev in reversed(stream_events):
            if _event_type(ev) != "response.completed":
                continue
            data = _event_payload(ev)
            if isinstance(data, dict):
                response_tool_names.extend(
                    _extract_response_tool_names((data.get("response") or {}).get("output") or [])
                )
                break

    # Error info
    error_msg = ""
    err_obj = resp_body.get("error")
    if isinstance(err_obj, dict):
        error_msg = err_obj.get("message", "")

    return {
        "turn": r.get("turn"),
        "request_id": r.get("request_id", ""),
        "timestamp": r.get("timestamp", ""),
        "duration_ms": r.get("duration_ms", 0),
        "method": req.get("method", ""),
        "path": req.get("path", ""),
        "model": body.get("model", ""),
        "status": resp.get("status", 0),
        "error_message": error_msg,
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
        "has_system": bool(sys_text),
        "message_count": len(msgs),
        "sys_hint": sys_text[:200],
        "tool_names": tool_names,
        "response_tool_names": response_tool_names,
    }


def _esc(value: object) -> str:
    return html_lib.escape("" if value is None else str(value), quote=True)


def _json_pretty(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _request(record: dict) -> dict:
    return _dict_or_empty(record.get("request"))


def _response(record: dict) -> dict:
    return _dict_or_empty(record.get("response"))


def _request_body(record: dict) -> dict:
    return _dict_or_empty(_request(record).get("body"))


def _response_body(record: dict) -> dict:
    return _dict_or_empty(_response(record).get("body"))


def _record_path(record: dict) -> str:
    request = _request(record)
    path = request.get("path")
    if isinstance(path, str) and path:
        return path
    url = request.get("url")
    if isinstance(url, str) and url:
        parsed = urlparse(url)
        return parsed.path or url
    return ""


def _is_bedrock_invoke_path(path: str) -> bool:
    return path.startswith("/model/") and (path.endswith("/invoke") or path.endswith("/invoke-with-response-stream"))


def _path_tier(path: str) -> int:
    if _is_bedrock_invoke_path(path):
        return 0
    if any(path.startswith(prefix) for prefix in PRIMARY_PATH_PREFIXES):
        return 0
    if any(path.startswith(prefix) for prefix in SECONDARY_PATH_PREFIXES):
        return 1
    return 2


def _active_paths_for_static_view(records: list[dict]) -> set[str]:
    paths = {_record_path(record) for record in records}
    primary = {path for path in paths if _path_tier(path) == 0}
    return primary or paths


def _filtered_records_for_static_view(records: list[dict]) -> list[dict]:
    active_paths = _active_paths_for_static_view(records)
    filtered = [record for record in records if _record_path(record) in active_paths]
    return sorted(filtered, key=lambda record: record.get("turn") or 0)


def _format_count(value: int) -> str:
    return f"{value:,}"


def _format_duration(duration_ms: object) -> str:
    try:
        ms = float(duration_ms or 0)
    except (TypeError, ValueError):
        ms = 0
    if ms < 1000:
        return f"{int(ms)}ms"
    seconds = ms / 1000
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    rem = int(seconds % 60)
    return f"{minutes}m {rem}s"


def _format_time(timestamp: object) -> str:
    if not isinstance(timestamp, str) or not timestamp:
        return ""
    normalized = timestamp.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).strftime("%H:%M:%S")
    except ValueError:
        return timestamp[-8:] if len(timestamp) >= 8 else timestamp


def _short_model(model: object) -> str:
    if not isinstance(model, str):
        return ""
    short = model
    if short.startswith("claude-"):
        short = short[len("claude-") :]
    if len(short) > 9 and short[-9] == "-" and short[-8:].isdigit():
        short = short[:-9]
    return short


def _model_badge_style(model: object) -> str:
    lower = model.lower() if isinstance(model, str) else ""
    if "opus" in lower:
        return "background:var(--purple-bg);color:var(--purple)"
    if "sonnet" in lower:
        return "background:var(--blue-bg);color:var(--blue)"
    if "haiku" in lower:
        return "background:var(--green-bg);color:var(--green)"
    return "background:var(--bg);color:var(--text-tertiary)"


def _task_label(record: dict) -> str:
    body = _request_body(record)
    sys_text = _extract_system_text(body)
    tools = body.get("tools") if isinstance(body.get("tools"), list) else []
    lower = sys_text.lower()
    if "you are opencode" in lower:
        return "OpenCode"
    if "you are codex" in lower:
        return "Codex"
    if "claude code" in lower:
        return "Claude Code"
    if "claude agent" in lower:
        return "Claude Agent"
    if "subagent" in lower or "sub-agent" in lower:
        return "Subagent"
    if "bash" in lower:
        return "Bash"
    if "explore" in lower:
        return "Explore"
    if "plan" in lower:
        return "Plan"
    if sys_text:
        for line in sys_text.splitlines():
            line = line.strip()
            if line and not line.lower().startswith("x-anthropic-billing-header:"):
                return line[:20].strip()
    if tools:
        return f"{len(tools)} tools"
    return ""


def _usage_from_record(record: dict) -> dict | None:
    body = _response_body(record)
    usage = body.get("usage")
    if not usage:
        for event in reversed(_iter_response_events(_response(record))):
            if _event_type(event) != "response.completed":
                continue
            data = _event_payload(event)
            if isinstance(data, dict):
                usage = (data.get("response") or {}).get("usage")
                if usage:
                    break
    return normalize_usage(usage or {})


def _stats_for_records(records: list[dict]) -> dict[str, int]:
    stats = {
        "turns": len(records),
        "total_tokens": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "duration_ms": 0,
    }
    for record in records:
        try:
            stats["duration_ms"] += int(record.get("duration_ms") or 0)
        except (TypeError, ValueError):
            pass
        usage = _usage_from_record(record)
        if not usage:
            continue
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        cache_read = int(usage.get("cache_read_input_tokens") or 0)
        cache_create = int(usage.get("cache_creation_input_tokens") or 0)
        stats["input_tokens"] += input_tokens
        stats["output_tokens"] += output_tokens
        stats["cache_read_input_tokens"] += cache_read
        stats["cache_creation_input_tokens"] += cache_create
        stats["total_tokens"] += input_tokens + output_tokens
    return stats


def _render_static_trace_path_bar(trace_path: Path, html_path: Path) -> str:
    copy_icon = (
        '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" '
        'height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>'
    )
    trace_text = str(trace_path.absolute())
    html_text = str(html_path.absolute())
    return (
        '<div class="trace-path-bar" id="trace-path-bar" style="display:flex">'
        f'<span class="tp-label">JSONL</span><span class="tp-val" title="{_esc(trace_text)}">{_esc(trace_text)}</span>'
        f'<button class="tp-copy" title="Copy path" onclick="copyPath(\'{_esc(trace_text)}\',this)">{copy_icon}</button>'
        '<span class="tp-sep"></span>'
        f'<span class="tp-label">HTML</span><span class="tp-val" title="{_esc(html_text)}">{_esc(html_text)}</span>'
        f'<button class="tp-copy" title="Copy path" onclick="copyPath(\'{_esc(html_text)}\',this)">{copy_icon}</button>'
        "</div>"
    )


def _render_static_path_filter(records: list[dict]) -> str:
    counts: dict[str, int] = {}
    for record in records:
        path = _record_path(record)
        counts[path] = counts.get(path, 0) + 1
    active_paths = _active_paths_for_static_view(records)
    chips = []
    for path in sorted(counts, key=lambda key: (_path_tier(key), -counts[key], key)):
        active = " active" if path in active_paths else ""
        label = f"…{path[-39:]}" if len(path) > 40 else path
        chips.append(
            f'<button class="filter-chip{active}" title="{_esc(path)}">'
            f'<span title="{_esc(path)}">{_esc(label)}</span><span class="chip-count">{counts[path]}</span></button>'
        )
    return '<div class="path-filter" id="path-filter" style="">' + "".join(chips) + "</div>"


def _render_static_sidebar_item(record: dict, idx: int, active: bool) -> str:
    request = _request(record)
    body = _request_body(record)
    response = _response(record)
    usage = _usage_from_record(record) or {}
    in_tokens = int(usage.get("input_tokens") or 0)
    out_tokens = int(usage.get("output_tokens") or 0)
    status = int(response.get("status") or 0)
    failed = status >= 400
    model = body.get("model") or ""
    task = _task_label(record)
    task_badge = (
        '<span class="si-task" style="background:var(--blue-bg);color:var(--blue)" '
        f'title="{_esc(task)}">{_esc(task)}</span>'
        if task
        else ""
    )
    error_dot = f'<span class="si-error-dot" title="HTTP {status}"></span>' if failed else ""
    classes = "sidebar-item" + (" is-error" if failed else "") + (" active" if active else "")
    border_color = "var(--red)" if failed else "var(--blue)"
    method_path = f"{request.get('method') or ''} {_record_path(record)}".strip()
    return f"""
    <div class="{classes}" data-idx="{idx}" style="border-left-color:{border_color}">
      <div class="si-row1">
        <span class="si-turn-wrap"><span class="si-turn">Turn {_esc(record.get("turn") or "?")}</span>{error_dot}</span>
        {task_badge}
        <span class="si-model" style="{_model_badge_style(model)}">{_esc(_short_model(model))}</span>
      </div>
      <div class="si-row2">
        <span class="si-tok">{_format_count(in_tokens + out_tokens)} tok</span>
        <span class="si-dur">{_esc(_format_duration(record.get("duration_ms") or 0))}</span>
        <span class="si-time">{_esc(_format_time(record.get("timestamp")))}</span>
      </div>
      <div class="si-path">{_esc(method_path)}</div>
    </div>"""


def _render_static_sidebar(records: list[dict]) -> str:
    items = [_render_static_sidebar_item(record, idx, idx == 0) for idx, record in enumerate(records)]
    return '<div class="sidebar" id="sidebar" style="flex:1;overflow-y:auto">' + "".join(items) + "</div>"


def _extract_system_text(body: dict) -> str:
    parts: list[str] = []
    system = body.get("system")
    if isinstance(system, str) and system.strip():
        parts.append(system)
    elif isinstance(system, list):
        system_parts = []
        for item in system:
            if isinstance(item, str):
                system_parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") == "text":
                    system_parts.append(str(item.get("text") or ""))
                else:
                    system_parts.append(_json_pretty(item))
        if any(part.strip() for part in system_parts):
            parts.append("\n\n".join(part for part in system_parts if part.strip()))
    instructions = body.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        parts.append(instructions)
    messages = body.get("messages")
    if isinstance(messages, list):
        for msg in messages:
            if not isinstance(msg, dict) or msg.get("role") not in {"system", "developer"}:
                continue
            text = _content_to_text(msg.get("content"))
            if text.strip():
                parts.append(text)
    return "\n\n".join(parts)


def _content_to_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") in {"text", "input_text", "output_text"}:
                    parts.append(str(item.get("text") or ""))
                elif isinstance(item.get("text"), str):
                    parts.append(item["text"])
                else:
                    parts.append(_json_pretty(item))
        return "\n".join(part for part in parts if part)
    if content is None:
        return ""
    return _json_pretty(content)


def _normalize_message_for_static_display(message: dict) -> dict:
    role = message.get("role") or "unknown"
    if role == "tool":
        return {
            **message,
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": message.get("tool_call_id") or "",
                    "content": message.get("content") or "",
                }
            ],
        }
    content = []
    raw_content = message.get("content")
    if isinstance(raw_content, list):
        content.extend(raw_content)
    elif isinstance(raw_content, str):
        if raw_content.strip():
            content.append({"type": "text", "text": raw_content})
    elif raw_content is not None:
        content.append(raw_content)
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            function = call.get("function") if isinstance(call.get("function"), dict) else {}
            content.append(
                {
                    "type": "tool_use",
                    "id": call.get("id") or "",
                    "name": function.get("name") or call.get("name") or "tool_use",
                    "input": _parse_function_call_arguments(function.get("arguments")),
                }
            )
    return {**message, "role": role, "content": content}


def _get_static_messages(body: dict) -> list[dict]:
    messages = body.get("messages")
    if isinstance(messages, list) and messages:
        return [
            _normalize_message_for_static_display(message)
            for message in messages
            if isinstance(message, dict) and message.get("role") not in {"system", "developer"}
        ]
    extracted = _extract_request_messages(body)
    if not extracted:
        return []
    has_instruction = any(message.get("role") in {"developer", "system"} for message in extracted)
    instructions = body.get("instructions")
    if not has_instruction and isinstance(instructions, str) and instructions.strip():
        return [{"role": "developer", "content": [{"type": "text", "text": instructions}]}] + extracted
    return extracted


def _is_context_only(record: dict, messages: list[dict], response_output: dict | None) -> bool:
    body = _request_body(record)
    if not messages or not isinstance(body.get("input"), list):
        return False
    return (
        "/codex/responses" in _record_path(record)
        or "/v1/responses" in _record_path(record)
        and response_output is None
    )


def _normalize_response_output(output: object) -> dict | None:
    if not isinstance(output, list) or not output:
        return None
    content = []
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message" and isinstance(item.get("content"), list):
            for block in item["content"]:
                if isinstance(block, dict) and block.get("type") == "output_text":
                    content.append({"type": "text", "text": block.get("text")})
                else:
                    content.append(block)
        elif _is_response_call_item(item):
            content.append(
                {"type": "tool_use", "name": _response_call_tool_name(item), "input": _response_call_input(item)}
            )
        elif item.get("type") == "reasoning" and item.get("summary"):
            summary = item.get("summary")
            if isinstance(summary, list):
                summary_text = "\n".join(str(part.get("text") or "") for part in summary if isinstance(part, dict))
            elif isinstance(summary, dict):
                summary_text = str(summary.get("text") or _json_pretty(summary))
            else:
                summary_text = str(summary)
            if summary_text.strip():
                content.append({"type": "thinking", "thinking": summary_text})
    return {"content": content} if content else None


def _get_static_response_output(record: dict) -> dict | None:
    body = _response_body(record)
    if isinstance(body.get("content"), list):
        return {"content": body["content"]}
    from_output = _normalize_response_output(body.get("output"))
    if from_output:
        return from_output
    events = _iter_response_events(_response(record))
    output_items = []
    for event in events:
        if _event_type(event) != "response.output_item.done":
            continue
        data = _event_payload(event)
        item = data.get("item") if isinstance(data, dict) else None
        output_index = data.get("output_index") if isinstance(data, dict) else None
        if isinstance(item, dict) and isinstance(output_index, int):
            output_items.append((output_index, item))
    if output_items:
        return _normalize_response_output([item for _, item in sorted(output_items)])
    for event in reversed(events):
        if _event_type(event) != "response.completed":
            continue
        data = _event_payload(event)
        response = data.get("response") if isinstance(data, dict) else None
        if isinstance(response, dict):
            normalized = _normalize_response_output(response.get("output"))
            if normalized:
                return normalized
    return None


def _render_static_content(content: object) -> str:
    if isinstance(content, str):
        return f'<div class="content-block">{_esc(content)}</div>' if content.strip() else ""
    if not isinstance(content, list):
        return "" if content is None else f"<pre>{_esc(_json_pretty(content))}</pre>"
    rendered = []
    for block in content:
        if not isinstance(block, dict):
            rendered.append(f"<pre>{_esc(_json_pretty(block))}</pre>")
            continue
        block_type = block.get("type")
        if block_type in {"text", "input_text", "output_text"}:
            text = str(block.get("text") or "")
            if text.strip():
                rendered.append(f'<div class="content-block">{_esc(text)}</div>')
        elif block_type == "thinking":
            thinking = str(block.get("thinking") or "")
            if thinking.strip():
                rendered.append(
                    '<div class="content-block"><span class="thinking-label">thinking</span>'
                    f'<div class="pre-text">{_esc(thinking)}</div></div>'
                )
        elif block_type == "tool_use":
            rendered.append(
                '<div class="content-block">'
                f'<span class="tool-use-label">{_esc(block.get("name") or "tool_use")}</span>'
                f"<pre>{_esc(_json_pretty(block.get('input') or {}))}</pre></div>"
            )
        elif block_type == "tool_result":
            result_content = block.get("content")
            if isinstance(result_content, str):
                rendered.append(
                    '<div class="content-block">'
                    f'<span class="tool-use-label">result ({_esc(block.get("tool_use_id") or "")})</span>'
                    f'<div class="pre-text">{_esc(result_content)}</div></div>'
                )
            else:
                rendered.append(f'<div class="content-block"><pre>{_esc(_json_pretty(block))}</pre></div>')
        else:
            rendered.append(f'<div class="content-block"><pre>{_esc(_json_pretty(block))}</pre></div>')
    return "".join(rendered)


def _render_static_messages(messages: list[dict]) -> str:
    rendered = []
    for message in messages:
        role = str(message.get("role") or "unknown")
        if role == "user":
            css_class = "user"
        elif role == "assistant":
            css_class = "assistant"
        elif role == "tool":
            css_class = "tool_result"
        else:
            css_class = "system"
        content = _render_static_content(message.get("content"))
        if content.strip():
            rendered.append(f'<div class="msg {css_class}"><div class="msg-role">{_esc(role)}</div>{content}</div>')
    return "".join(rendered)


def _tool_description(tool: dict) -> str:
    description = tool.get("description")
    if isinstance(description, str):
        return description
    function = tool.get("function")
    if isinstance(function, dict) and isinstance(function.get("description"), str):
        return function["description"]
    return ""


def _tool_schema(tool: dict) -> dict:
    schema = tool.get("input_schema")
    if isinstance(schema, dict):
        return schema
    schema = tool.get("parameters")
    if isinstance(schema, dict):
        return schema
    function = tool.get("function")
    if isinstance(function, dict) and isinstance(function.get("parameters"), dict):
        return function["parameters"]
    return {}


def _render_static_tools(tools: list[dict]) -> str:
    blocks = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = _tool_display_name(tool) or "unknown"
        description = _tool_description(tool)
        short_description = description.split("\n", 1)[0][:120]
        schema = _tool_schema(tool)
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        required = set(schema.get("required") or [])
        params = []
        for key, prop in properties.items():
            prop = prop if isinstance(prop, dict) else {}
            prop_type = prop.get("type") or ("enum" if prop.get("enum") else "")
            required_html = '<span class="tb-prequired">required</span>' if key in required else ""
            type_html = f'<span class="tb-ptype">{_esc(prop_type)}</span>' if prop_type else ""
            desc_html = (
                f'<div class="tb-pdesc">{_esc(prop.get("description") or "")}</div>' if prop.get("description") else ""
            )
            params.append(
                '<div class="tb-param"><div class="tb-param-row1">'
                f'<span class="tb-pname">{_esc(key)}</span>{type_html}{required_html}</div>{desc_html}</div>'
            )
        params_html = '<div class="tb-params-title">Params</div>' + "".join(params) if params else ""
        body = f'<div class="tb-full-desc">{_esc(description)}</div>' if description else ""
        blocks.append(
            '<div class="tool-block"><div class="tool-block-header">'
            f'<span class="tb-arrow open">&#9654;</span><span class="tb-name">{_esc(name)}</span>'
            f'<span class="tb-desc">{_esc(short_description)}</span></div>'
            f'<div class="tool-block-body open">{body}{params_html}</div></div>'
        )
    return "".join(blocks)


def _render_static_token_usage(usage: dict) -> str:
    items = [
        ("Input", usage.get("input_tokens") or 0, "var(--blue)"),
        ("Output", usage.get("output_tokens") or 0, "var(--green)"),
        ("Cache read", usage.get("cache_read_input_tokens") or 0, "var(--cyan)"),
        ("Cache create", usage.get("cache_creation_input_tokens") or 0, "var(--amber)"),
    ]
    return (
        '<div class="token-bar">'
        + "".join(
            '<div class="tok-item">'
            f'<span class="tok-dot" style="background:{color}"></span><span class="tok-label">{label}</span>'
            f'<span class="tok-val">{_format_count(int(value))}</span></div>'
            for label, value, color in items
        )
        + "</div>"
    )


def _render_static_events(events: list[dict]) -> str:
    rendered = []
    for event in events:
        event_type = _event_type(event) or "event"
        payload = event.get("data", event)
        data = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        short = data[:200] + "..." if len(data) > 200 else data
        rendered.append(
            f'<div class="sse-event"><span class="sse-type">{_esc(event_type)}</span><span class="sse-data">{_esc(short)}</span></div>'
        )
    return "".join(rendered)


def _static_section(title: str, body: str, badge: str | None = None, open_body: bool = True) -> str:
    open_class = " open" if open_body else ""
    chevron_class = "chevron open" if open_body else "chevron"
    badge_html = f'<span class="badge">{_esc(badge)}</span>' if badge else ""
    return (
        '<div class="section"><div class="section-header">'
        f'<span class="{chevron_class}">&#9654;</span><span class="title">{_esc(title)}</span>{badge_html}</div>'
        f'<div class="section-body{open_class}">{body}</div></div>'
    )


def _render_static_detail(record: dict | None) -> str:
    if not record:
        return '<div class="empty-state">No trace entries found.</div>'
    request_body = _request_body(record)
    response = _response(record)
    status = int(response.get("status") or 0)
    html_parts = []
    if status >= 400:
        error = _response_body(record).get("error")
        message = error.get("message") if isinstance(error, dict) else "Unknown error"
        html_parts.append(
            '<div class="error-banner"><div class="eb-icon">&#9888;</div><div class="eb-content">'
            f'<div class="eb-title">HTTP {status}</div><div class="eb-message">{_esc(message)}</div></div></div>'
        )
    html_parts.append(
        '<div class="action-bar">'
        '<button class="act-btn" onclick="copyRequestBody(this)">Request JSON</button>'
        '<button class="act-btn" onclick="copyCurl(this)">cURL</button>'
        '<button class="act-btn" onclick="showDiff(this)">Diff with Prev</button>'
        "</div>"
    )
    usage = _usage_from_record(record)
    if usage:
        html_parts.append(_render_static_token_usage(usage))
    tools = request_body.get("tools")
    if isinstance(tools, list) and tools:
        html_parts.append(_static_section("Tools", _render_static_tools(tools), f"{len(tools)} tools"))
    system_text = _extract_system_text(request_body)
    if system_text:
        html_parts.append(_static_section("System", f'<div class="pre-text">{_esc(system_text)}</div>'))
    response_output = _get_static_response_output(record)
    messages = _get_static_messages(request_body)
    context_only = _is_context_only(record, messages, response_output)
    if messages:
        title = "Context" if context_only else "Messages"
        html_parts.append(_static_section(title, _render_static_messages(messages), f"{len(messages)} messages"))
    if response_output or context_only:
        if response_output and response_output.get("content"):
            response_html = _render_static_content(response_output["content"])
        else:
            response_html = (
                '<em style="color:var(--text-tertiary)">Request context only; no standalone response content.</em>'
            )
        html_parts.append(_static_section("Response", response_html))
    events = _iter_response_events(response)
    if events:
        html_parts.append(
            _static_section("SSE", _render_static_events(events), f"{len(events)} events", open_body=False)
        )
    json_html = f'<div class="json-view">{_esc(_json_pretty(record))}</div>'
    html_parts.append(_static_section("JSON", json_html, open_body=False))
    return "".join(html_parts)


def _static_records_from_json(records: list[str]) -> list[dict]:
    parsed = []
    for record_json in records:
        try:
            record = json.loads(record_json)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(record, dict):
            parsed.append(record)
    return parsed


def _inject_static_viewer_dom(html: str, records: list[str], trace_path: Path, html_path: Path) -> str:
    parsed_records = _static_records_from_json(records)
    if not parsed_records:
        return html
    filtered_records = _filtered_records_for_static_view(parsed_records)
    stats = _stats_for_records(filtered_records)
    detail_record = filtered_records[0] if filtered_records else None

    html = html.replace(
        '<div class="path-filter" id="path-filter" style="display:none"></div>',
        _render_static_path_filter(parsed_records),
        1,
    )
    html = html.replace(
        '<div class="stats" id="stats" style="display:none">', '<div class="stats" id="stats" style="">', 1
    )
    html = html.replace(
        '<span class="stat-val" id="stat-turns">0</span>',
        f'<span class="stat-val" id="stat-turns">{stats["turns"]}</span>',
        1,
    )
    html = html.replace(
        '<span class="stat-val" id="stat-tokens">0</span>',
        f'<span class="stat-val" id="stat-tokens">{_format_count(stats["total_tokens"])}</span>',
        1,
    )
    if stats["total_tokens"]:
        html = html.replace(
            'id="stat-input-group" style="display:none"', 'id="stat-input-group" style="display:flex"', 1
        )
        html = html.replace(
            'id="stat-output-group" style="display:none"', 'id="stat-output-group" style="display:flex"', 1
        )
        html = html.replace(
            '<span class="stat-val" id="stat-input">0</span>',
            f'<span class="stat-val" id="stat-input">{_format_count(stats["input_tokens"])}</span>',
            1,
        )
        html = html.replace(
            '<span class="stat-val" id="stat-output">0</span>',
            f'<span class="stat-val" id="stat-output">{_format_count(stats["output_tokens"])}</span>',
            1,
        )
    if stats["cache_read_input_tokens"]:
        html = html.replace(
            'id="stat-cache-read-group" style="display:none"', 'id="stat-cache-read-group" style="display:flex"', 1
        )
        html = html.replace(
            '<span class="stat-val" id="stat-cache-read">0</span>',
            f'<span class="stat-val" id="stat-cache-read">{_format_count(stats["cache_read_input_tokens"])}</span>',
            1,
        )
    if stats["cache_creation_input_tokens"]:
        html = html.replace(
            'id="stat-cache-write-group" style="display:none"', 'id="stat-cache-write-group" style="display:flex"', 1
        )
        html = html.replace(
            '<span class="stat-val" id="stat-cache-write">0</span>',
            f'<span class="stat-val" id="stat-cache-write">{_format_count(stats["cache_creation_input_tokens"])}</span>',
            1,
        )
    html = html.replace(
        '<span class="stat-val" id="stat-duration">0s</span>',
        f'<span class="stat-val" id="stat-duration">{_esc(_format_duration(stats["duration_ms"]))}</span>',
        1,
    )
    html = html.replace(
        '<div class="trace-path-bar" id="trace-path-bar"></div>',
        _render_static_trace_path_bar(trace_path, html_path),
        1,
    )
    html = html.replace(
        '<div class="drop-zone" id="drop-zone">', '<div class="drop-zone" id="drop-zone" style="display:none">', 1
    )
    html = html.replace(
        '<span>Turn <span class="pi-current" id="pi-current">0</span> <span class="pi-total" id="pi-total">of 0</span></span>',
        f'<span>Turn <span class="pi-current" id="pi-current">1</span> <span class="pi-total" id="pi-total">of {len(filtered_records)}</span></span>',
        1,
    )
    html = html.replace(
        '<div class="pi-bar"><div class="pi-fill" id="pi-fill"></div></div>',
        '<div class="pi-bar"><div class="pi-fill" id="pi-fill" style="width:100%"></div></div>',
        1,
    )
    html = html.replace(
        '<div class="sidebar" id="sidebar" style="flex:1;overflow-y:auto"></div>',
        _render_static_sidebar(filtered_records),
        1,
    )
    html = html.replace(
        '<div class="detail" id="detail" style="display:none"></div>',
        f'<div class="detail" id="detail" data-static-preload="1" style="">{_render_static_detail(detail_record)}</div>',
        1,
    )
    return html


def _generate_html_viewer(trace_path: Path, html_path: Path) -> None:
    """Read viewer.html template, embed JSONL data, write self-contained HTML."""
    template = Path(__file__).parent / "viewer.html"
    if not template.exists():
        return

    # Read JSONL records
    records: list[str] = []
    if trace_path.exists():
        with open(trace_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(_normalize_record_for_viewer(line))

    # Escape </ sequences so embedded record JSON cannot prematurely close the
    # surrounding <script> / <script type="text/plain"> blocks. Forward-proxy
    # mode can capture arbitrary HTTPS upstreams whose bodies legitimately
    # contain </script>; without this, the browser closes the data block early
    # and renders the captured HTML as page content. JSON's \/ is a valid
    # escape for /, so the parsed JSON value is unchanged.
    records = [rec.replace("</", "<\\/") for rec in records]

    jsonl_path_js = json.dumps(str(trace_path.absolute()))
    html_path_js = json.dumps(str(html_path.absolute()))
    version_js = json.dumps(CLAUDE_TAP_VERSION)

    use_lazy = len(records) > LAZY_THRESHOLD

    if use_lazy:
        # Extract metadata for sidebar rendering
        meta_list = []
        for rec in records:
            meta = _extract_metadata(rec)
            if meta is not None:
                meta_list.append(meta)

        meta_js = json.dumps(meta_list, separators=(",", ":"))

        raw_lines = "\n".join(records)

        data_js = (
            f"const EMBEDDED_TRACE_META = {meta_js};\n"
            f"const __TRACE_JSONL_PATH__ = {jsonl_path_js};\n"
            f"const __TRACE_HTML_PATH__ = {html_path_js};\n"
            f"const __CLAUDE_TAP_VERSION__ = {version_js};\n"
        )

        html = template.read_text(encoding="utf-8")
        # Inject data script + raw JSONL block before the main <script> tag
        html = html.replace(
            "<script>\nconst $ = s =>",
            f"<script>\n{data_js}</script>\n"
            f'<script type="text/plain" id="trace-raw">\n{raw_lines}\n</script>\n'
            "<script>\nconst $ = s =>",
            1,
        )
        html = _inject_static_viewer_dom(html, records, trace_path, html_path)
    else:
        # Small trace: inline all data as before
        data_js = (
            "const EMBEDDED_TRACE_DATA = [\n" + ",\n".join(records) + "\n];\n"
            f"const __TRACE_JSONL_PATH__ = {jsonl_path_js};\n"
            f"const __TRACE_HTML_PATH__ = {html_path_js};\n"
            f"const __CLAUDE_TAP_VERSION__ = {version_js};\n"
        )

        html = template.read_text(encoding="utf-8")
        html = html.replace(
            "<script>\nconst $ = s =>",
            f"<script>\n{data_js}</script>\n<script>\nconst $ = s =>",
            1,
        )
        html = _inject_static_viewer_dom(html, records, trace_path, html_path)

    html_path.write_text(html, encoding="utf-8")
