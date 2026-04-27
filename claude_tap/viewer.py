"""HTML viewer generation – embed JSONL data into a self-contained HTML file."""

from __future__ import annotations

import base64
import json
from importlib.metadata import version as _pkg_version
from pathlib import Path

from claude_tap.sse import SSEReassembler

try:
    CLAUDE_TAP_VERSION = _pkg_version("claude-tap")
except Exception:
    CLAUDE_TAP_VERSION = "0.0.0"

# Threshold: traces with more entries than this use lazy mode
LAZY_THRESHOLD = 50


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
        if item.get("type") not in (None, "message") and "role" not in item:
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
        elif item.get("type") == "function_call":
            names.append(item.get("name", ""))
    return names


def _extract_metadata(record_json: str) -> dict | None:
    """Extract sidebar-relevant metadata from a raw JSON record string.

    Returns a lightweight dict with only the fields needed for sidebar
    rendering, filtering, and search — avoiding full parse of large records.
    """
    try:
        r = json.loads(record_json)
    except (json.JSONDecodeError, TypeError):
        return None

    req = r.get("request") or {}
    body = req.get("body") or {}
    resp = r.get("response") or {}
    resp_body = resp.get("body") or {}
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
    tool_names = [t.get("name", "") for t in tools if isinstance(t, dict)]

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

        # Escape </ sequences in raw JSONL to prevent premature </script> close.
        # In JSON, \/ is a valid escape for /, so replacing </ with <\/ is safe.
        raw_lines = "\n".join(rec.replace("</", "<\\/") for rec in records)

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

    html_path.write_text(html, encoding="utf-8")
