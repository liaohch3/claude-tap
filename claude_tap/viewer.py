"""HTML viewer generation – embed JSONL data into a self-contained HTML file."""

from __future__ import annotations

import base64
import json
from importlib.metadata import version as _pkg_version
from pathlib import Path

from claude_tap.sse import SSEReassembler
from claude_tap.usage import normalize_usage

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


def _parse_function_call_arguments(arguments: object) -> object:
    if isinstance(arguments, str):
        try:
            return json.loads(arguments)
        except (json.JSONDecodeError, TypeError):
            return arguments
    if arguments is None:
        return {}
    return arguments


def _parse_sse_data_frames(body: object) -> list[dict]:
    if not isinstance(body, str) or "data:" not in body:
        return []

    events: list[dict] = []
    data_lines: list[str] = []
    for line in body.splitlines():
        if line.startswith("data:"):
            data_lines.append(line[len("data:") :].strip())
            continue
        if line.strip():
            continue
        if not data_lines:
            continue
        raw_data = "\n".join(data_lines)
        data_lines = []
        if raw_data == "[DONE]":
            continue
        try:
            data = json.loads(raw_data)
        except (json.JSONDecodeError, TypeError):
            data = raw_data
        events.append({"event": "message", "data": data})

    if data_lines:
        raw_data = "\n".join(data_lines)
        if raw_data != "[DONE]":
            try:
                data = json.loads(raw_data)
            except (json.JSONDecodeError, TypeError):
                data = raw_data
            events.append({"event": "message", "data": data})

    return events


def _gemini_request(body: dict) -> dict:
    req = body.get("request")
    return req if isinstance(req, dict) else {}


def _is_gemini_request_body(body: dict) -> bool:
    req = _gemini_request(body)
    return bool(req) and (
        isinstance(req.get("contents"), list)
        or isinstance(req.get("systemInstruction"), dict)
        or isinstance(req.get("tools"), list)
    )


def _gemini_text_from_parts(parts: object) -> str:
    if not isinstance(parts, list):
        return ""
    return "\n".join(
        part.get("text", "") for part in parts if isinstance(part, dict) and isinstance(part.get("text"), str)
    )


def _extract_gemini_system(body: dict) -> str:
    instr = _gemini_request(body).get("systemInstruction")
    if not isinstance(instr, dict):
        return ""
    return _gemini_text_from_parts(instr.get("parts")).strip()


def _gemini_function_response_content(resp: dict) -> str:
    payload = resp.get("response")
    if isinstance(payload, dict) and "output" in payload:
        output = payload["output"]
    else:
        output = payload
    if isinstance(output, str):
        return output
    return json.dumps(output, ensure_ascii=False)


def _gemini_part_blocks(part: dict) -> list[dict]:
    blocks: list[dict] = []
    text = part.get("text")
    if isinstance(text, str) and text.strip():
        if part.get("thought") is True:
            blocks.append({"type": "thinking", "thinking": text})
        else:
            blocks.append({"type": "text", "text": text})

    call = part.get("functionCall")
    if isinstance(call, dict):
        blocks.append(
            {
                "type": "tool_use",
                "id": call.get("id", ""),
                "name": call.get("name", "tool_use"),
                "input": call.get("args") if isinstance(call.get("args"), dict) else {},
            }
        )

    response = part.get("functionResponse")
    if isinstance(response, dict):
        blocks.append(
            {
                "type": "tool_result",
                "tool_use_id": response.get("id") or response.get("name", ""),
                "content": _gemini_function_response_content(response),
            }
        )
    return blocks


def _gemini_role(role: object) -> str:
    if role == "model":
        return "assistant"
    return role if isinstance(role, str) and role else "user"


def _extract_gemini_request_messages(body: dict) -> list[dict]:
    contents = _gemini_request(body).get("contents")
    if not isinstance(contents, list):
        return []

    messages: list[dict] = []
    for content in contents:
        if not isinstance(content, dict):
            continue
        blocks: list[dict] = []
        for part in content.get("parts") or []:
            if isinstance(part, dict):
                blocks.extend(_gemini_part_blocks(part))
        if not blocks:
            continue
        role = _gemini_role(content.get("role"))
        if all(block.get("type") == "tool_result" for block in blocks):
            role = "tool"
        messages.append({"role": role, "content": blocks})
    return messages


def _extract_gemini_tools(body: dict) -> list[dict]:
    tools = _gemini_request(body).get("tools")
    if not isinstance(tools, list):
        return []

    normalized: list[dict] = []
    for tool_group in tools:
        if not isinstance(tool_group, dict):
            continue
        declarations = tool_group.get("functionDeclarations")
        if not isinstance(declarations, list):
            continue
        for decl in declarations:
            if not isinstance(decl, dict):
                continue
            normalized.append(
                {
                    "name": decl.get("name", ""),
                    "description": decl.get("description", ""),
                    "input_schema": decl.get("parametersJsonSchema") or decl.get("parameters") or {},
                }
            )
    return normalized


def _gemini_payloads_from_response_body(body: object) -> list[dict]:
    if isinstance(body, str):
        return [event["data"] for event in _parse_sse_data_frames(body) if isinstance(event.get("data"), dict)]
    if isinstance(body, dict):
        return [body]
    return []


def _extract_gemini_response_output(body: object) -> dict | None:
    payloads = _gemini_payloads_from_response_body(body)
    content: list[dict] = []
    text_block: dict[str, str] | None = None

    def append_text(text: str) -> None:
        nonlocal text_block
        if not text.strip():
            return
        if text_block is None:
            text_block = {"type": "text", "text": ""}
            content.append(text_block)
        text_block["text"] += text

    for payload in payloads:
        response = payload.get("response") if isinstance(payload.get("response"), dict) else payload
        candidates = response.get("candidates") if isinstance(response, dict) else None
        if not isinstance(candidates, list):
            continue
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            candidate_content = candidate.get("content")
            if not isinstance(candidate_content, dict):
                continue
            for part in candidate_content.get("parts") or []:
                if not isinstance(part, dict):
                    continue
                if isinstance(part.get("text"), str):
                    if part.get("thought") is True:
                        thinking = part["text"]
                        if thinking.strip():
                            content.append({"type": "thinking", "thinking": thinking})
                    else:
                        append_text(part["text"])
                call = part.get("functionCall")
                if isinstance(call, dict):
                    content.append(
                        {
                            "type": "tool_use",
                            "id": call.get("id", ""),
                            "name": call.get("name", "tool_use"),
                            "input": call.get("args") if isinstance(call.get("args"), dict) else {},
                        }
                    )

    return {"content": content} if content else None


def _extract_gemini_response_usage(body: object) -> dict:
    usage: dict = {}
    for payload in _gemini_payloads_from_response_body(body):
        response = payload.get("response") if isinstance(payload.get("response"), dict) else payload
        if not isinstance(response, dict):
            continue
        event_usage = response.get("usageMetadata")
        if isinstance(event_usage, dict):
            usage = event_usage
    return normalize_usage(usage)


def _extract_gemini_response_tool_names(body: object) -> list[str]:
    output = _extract_gemini_response_output(body)
    if not output:
        return []
    return [block.get("name", "") for block in output["content"] if block.get("type") == "tool_use"]


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

    if _is_gemini_request_body(body):
        return _extract_gemini_request_messages(body)

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
    raw_resp_body = resp.get("body")
    resp_body = _dict_or_empty(raw_resp_body)
    stream_events = _iter_response_events(resp)
    if not stream_events:
        stream_events = _parse_sse_data_frames(raw_resp_body)

    # Token usage — from response.body.usage or terminal stream event
    usage = resp_body.get("usage") or _extract_gemini_response_usage(raw_resp_body) or {}
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
    elif _is_gemini_request_body(body):
        sys_text = _extract_gemini_system(body)

    # Messages
    msgs = _extract_request_messages(body)

    # Tool names from request
    tools = body.get("tools") or _extract_gemini_tools(body)
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
        response_tool_names.extend(_extract_gemini_response_tool_names(raw_resp_body))
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
