"""Export trace JSONL files to Markdown, JSON, or HTML format."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from claude_tap.viewer import _generate_html_viewer, _normalize_record_for_viewer


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _normalize_record_for_export(record: object) -> dict | None:
    if not isinstance(record, dict):
        return None
    try:
        normalized = json.loads(_normalize_record_for_viewer(json.dumps(record, ensure_ascii=False)))
    except (TypeError, json.JSONDecodeError):
        return record
    return normalized if isinstance(normalized, dict) else record


def _request_body(record: dict) -> dict:
    return _as_dict(_as_dict(record.get("request")).get("body"))


def _response_body(record: dict) -> dict:
    return _as_dict(_as_dict(record.get("response")).get("body"))


def _usage_from(record: dict) -> dict:
    return _as_dict(_response_body(record).get("usage"))


def _turn_sort_key(record: dict) -> int:
    turn = record.get("turn")
    return turn if isinstance(turn, int) else 0


def export_main(argv: list[str] | None = None) -> int:
    """Entry point for the export subcommand."""
    parser = argparse.ArgumentParser(
        prog="claude-tap export",
        description="Export a trace JSONL file to Markdown, JSON, or HTML.",
    )
    parser.add_argument("trace_file", type=Path, help="Path to the .jsonl trace file")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output file path (default: stdout; for HTML, trace_file with .html suffix)",
    )
    parser.add_argument(
        "--format",
        choices=["markdown", "json", "html"],
        default=None,
        help="Output format (default: inferred from -o extension, or markdown)",
    )

    args = parser.parse_args(argv)

    if not args.trace_file.exists():
        print(f"Error: trace file not found: {args.trace_file}", file=sys.stderr)
        return 1

    # Read records
    records = []
    with open(args.trace_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    record = _normalize_record_for_export(json.loads(line))
                    if record is not None:
                        records.append(record)
                except json.JSONDecodeError:
                    continue

    if not records:
        print("Error: no valid records found in trace file", file=sys.stderr)
        return 1

    # Sort by turn
    records.sort(key=_turn_sort_key)

    # Determine format
    fmt = args.format
    if fmt is None:
        if args.output:
            suffix = args.output.suffix.lower()
            if suffix == ".json":
                fmt = "json"
            elif suffix in {".html", ".htm"}:
                fmt = "html"
            else:
                fmt = "markdown"
        else:
            fmt = "markdown"

    if fmt == "html":
        html_path = args.output or args.trace_file.with_suffix(".html")
        _generate_html_viewer(args.trace_file, html_path)
        if not html_path.exists():
            print("Error: failed to generate HTML viewer", file=sys.stderr)
            return 1
        print(f"Exported {len(records)} turns to {html_path}")
        return 0

    if fmt == "json":
        output = _export_json(records)
    else:
        output = _export_markdown(records)

    if args.output:
        args.output.write_text(output, encoding="utf-8")
        print(f"Exported {len(records)} turns to {args.output}")
    else:
        print(output)

    return 0


def _export_markdown(records: list[dict]) -> str:
    """Export records as Markdown."""
    lines: list[str] = []
    lines.append("# Claude Trace Export\n")

    # Token summary
    total_input = 0
    total_output = 0
    total_cache_read = 0
    total_cache_create = 0
    models: set[str] = set()

    for r in records:
        usage = _usage_from(r)
        total_input += usage.get("input_tokens", 0)
        total_output += usage.get("output_tokens", 0)
        total_cache_read += usage.get("cache_read_input_tokens", 0)
        total_cache_create += usage.get("cache_creation_input_tokens", 0)
        model = _request_body(r).get("model", "")
        if model:
            models.add(model)

    lines.append("## Summary\n")
    lines.append(f"- **Turns**: {len(records)}")
    lines.append(f"- **Models**: {', '.join(sorted(models)) if models else 'unknown'}")
    lines.append(f"- **Input tokens**: {total_input:,}")
    lines.append(f"- **Output tokens**: {total_output:,}")
    if total_cache_read:
        lines.append(f"- **Cache read tokens**: {total_cache_read:,}")
    if total_cache_create:
        lines.append(f"- **Cache create tokens**: {total_cache_create:,}")
    lines.append("")

    # Each turn
    for r in records:
        turn = r.get("turn", "?")
        req_body = _request_body(r)
        resp_body = _response_body(r)
        model = req_body.get("model", "unknown")
        duration = r.get("duration_ms", 0)

        lines.append(f"---\n\n## Turn {turn}\n")
        lines.append(f"**Model**: `{model}` | **Duration**: {duration}ms\n")

        # User messages (last message from request)
        messages = req_body.get("messages", [])
        if isinstance(messages, list) and messages:
            last_msg = messages[-1]
            if isinstance(last_msg, dict):
                role = last_msg.get("role", "unknown")
                lines.append(f"### {role.title()}\n")
                content = last_msg.get("content", "")
                if isinstance(content, str):
                    lines.append(content + "\n")
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict):
                            if block.get("type") == "text":
                                lines.append(block.get("text", "") + "\n")
                            elif block.get("type") == "tool_result":
                                lines.append(f"**Tool Result** (`{block.get('tool_use_id', '')}`)\n")
                                rc = block.get("content", "")
                                if isinstance(rc, str):
                                    lines.append(f"```\n{rc[:2000]}\n```\n")
                                elif isinstance(rc, list):
                                    for sub in rc:
                                        if isinstance(sub, dict) and sub.get("type") == "text":
                                            lines.append(f"```\n{sub.get('text', '')[:2000]}\n```\n")

        # Response
        resp_content = resp_body.get("content", [])
        if isinstance(resp_content, list) and resp_content:
            lines.append("### Assistant\n")
            for block in resp_content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text = block.get("text", "")
                        if text.strip():
                            lines.append(text + "\n")
                    elif block.get("type") == "tool_use":
                        name = block.get("name", "unknown")
                        inp = block.get("input", {})
                        lines.append(f"**Tool Use**: `{name}`\n")
                        lines.append(f"```json\n{json.dumps(inp, indent=2, ensure_ascii=False)[:3000]}\n```\n")
                    elif block.get("type") == "thinking":
                        thinking = block.get("thinking", "")
                        if thinking.strip():
                            lines.append(f"<details>\n<summary>Thinking</summary>\n\n{thinking[:5000]}\n\n</details>\n")

        # Token usage
        usage = _as_dict(resp_body.get("usage"))
        if usage:
            parts = []
            if usage.get("input_tokens"):
                parts.append(f"in={usage['input_tokens']:,}")
            if usage.get("output_tokens"):
                parts.append(f"out={usage['output_tokens']:,}")
            if usage.get("cache_read_input_tokens"):
                parts.append(f"cache_read={usage['cache_read_input_tokens']:,}")
            if usage.get("cache_creation_input_tokens"):
                parts.append(f"cache_create={usage['cache_creation_input_tokens']:,}")
            if parts:
                lines.append(f"*Tokens: {' / '.join(parts)}*\n")

    return "\n".join(lines)


def _export_json(records: list[dict]) -> str:
    """Export records as cleaned-up JSON."""
    cleaned = []
    for r in records:
        req_body = _request_body(r)
        resp_body = _response_body(r)

        entry = {
            "turn": r.get("turn"),
            "timestamp": r.get("timestamp"),
            "duration_ms": r.get("duration_ms"),
            "model": req_body.get("model"),
            "messages": req_body.get("messages") if isinstance(req_body.get("messages"), list) else [],
            "response": {
                "content": resp_body.get("content") if isinstance(resp_body.get("content"), list) else [],
                "usage": _as_dict(resp_body.get("usage")),
                "stop_reason": resp_body.get("stop_reason"),
            },
        }

        # Include system prompt if present
        system = req_body.get("system")
        if system:
            entry["system"] = system

        # Include tools if present
        tools = req_body.get("tools")
        if tools:
            entry["tools"] = tools

        cleaned.append(entry)

    return json.dumps(cleaned, indent=2, ensure_ascii=False)
