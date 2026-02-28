"""HTML viewer generation – embed JSONL data into a self-contained HTML file."""

from __future__ import annotations

import json
from importlib.metadata import version as _pkg_version
from pathlib import Path

try:
    CLAUDE_TAP_VERSION = _pkg_version("claude-tap")
except Exception:
    CLAUDE_TAP_VERSION = "0.0.0"

# Threshold: traces with more entries than this use lazy mode
LAZY_THRESHOLD = 50


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

    # Token usage — from response.body.usage or SSE response.completed
    usage = resp_body.get("usage") or {}
    if not usage:
        sse = resp.get("sse_events") or []
        for ev in reversed(sse):
            if ev.get("event") == "response.completed":
                data = ev.get("data")
                if isinstance(data, str):
                    try:
                        data = json.loads(data)
                    except (json.JSONDecodeError, TypeError):
                        continue
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
    msgs = body.get("messages") or []
    if not msgs:
        inp = body.get("input") or []
        msgs = [item for item in inp if isinstance(item, dict) and item.get("type") == "message"]

    # Tool names from request
    tools = body.get("tools") or []
    tool_names = [t.get("name", "") for t in tools if isinstance(t, dict)]

    # Response tool names (tool_use blocks in response content)
    response_tool_names = []
    # Try response.body.content first
    rc = resp_body.get("content") or []
    if not rc:
        # Try SSE response.completed
        sse = resp.get("sse_events") or []
        for ev in reversed(sse):
            if ev.get("event") == "response.completed":
                data = ev.get("data")
                if isinstance(data, str):
                    try:
                        data = json.loads(data)
                    except (json.JSONDecodeError, TypeError):
                        continue
                if isinstance(data, dict):
                    output = (data.get("response") or {}).get("output") or []
                    for item in output:
                        if isinstance(item, dict):
                            if item.get("type") == "message":
                                for c in item.get("content") or []:
                                    if isinstance(c, dict) and c.get("type") == "tool_use":
                                        response_tool_names.append(c.get("name", ""))
                            elif item.get("type") == "function_call":
                                response_tool_names.append(item.get("name", ""))
                    break
    else:
        for block in rc:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                response_tool_names.append(block.get("name", ""))

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
                    records.append(line)

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
