"""Export trace JSONL files to Markdown, JSON, or HTML format."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from claude_tap.compact_trace import build_compact_trace_bundle, dump_compact_trace, is_compact_trace_bundle
from claude_tap.prompt_snapshot import render_prompt_markdown, snapshot_from_records
from claude_tap.usage import normalize_usage
from claude_tap.viewer import (
    _generate_html_viewer_from_compact_bundle,
    _generate_html_viewer_from_records,
    _normalize_record_for_viewer,
)


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


def _normalize_records_for_export(records: list[dict]) -> list[dict]:
    normalized_records: list[dict] = []
    for record in records:
        normalized = _normalize_record_for_export(record)
        if normalized is not None:
            normalized_records.append(normalized)
    return normalized_records


def _request_body(record: dict) -> dict:
    return _as_dict(_as_dict(record.get("request")).get("body"))


def _response_body(record: dict) -> dict:
    return _as_dict(_as_dict(record.get("response")).get("body"))


def _usage_from(record: dict) -> dict:
    return normalize_usage(_response_body(record).get("usage"))


def _turn_sort_key(record: dict) -> int:
    turn = record.get("turn")
    return turn if isinstance(turn, int) else 0


def _compact_output_suffix(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith(".ctap") or name.endswith(".ctap.json") or name.endswith(".compact.json")


def _load_records_from_text(text: str) -> tuple[list[dict], dict | None]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if is_compact_trace_bundle(parsed):
        from claude_tap.compact_trace import materialize_compact_trace_bundle

        return materialize_compact_trace_bundle(parsed), parsed

    records: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records, None


def export_main(argv: list[str] | None = None) -> int:
    """Entry point for the export subcommand."""
    parser = argparse.ArgumentParser(
        prog="claude-tap export",
        description="Export a trace file or SQLite session to Markdown, JSON, HTML, or compact trace.",
    )
    parser.add_argument("source", type=str, nargs="?", help="Path to a .jsonl trace file or a SQLite session id")
    parser.add_argument(
        "--session-id",
        dest="session_id",
        help="Export a stored SQLite session by id",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output file path (default: stdout; for HTML, trace_file with .html suffix)",
    )
    parser.add_argument(
        "--format",
        choices=["markdown", "json", "html", "compact", "prompt-md"],
        default=None,
        help="Output format (default: inferred from -o extension, or markdown)",
    )

    args = parser.parse_args(argv)

    records: list[dict] = []
    html_source_path: Path | None = None
    source_session_id = args.session_id
    store = None
    compact_bundle: dict | None = None

    if source_session_id is None and args.source:
        trace_file = Path(args.source)
        if not trace_file.exists():
            from claude_tap.trace_store import get_trace_store

            store = get_trace_store()
            if store.load_session_row(args.source) is not None:
                source_session_id = args.source

    if source_session_id:
        from claude_tap.trace_store import get_trace_store

        if store is None:
            store = get_trace_store()
        if store.load_session_row(source_session_id) is None:
            print(f"Error: session not found: {source_session_id}", file=sys.stderr)
            return 1
        for record in store.load_records(source_session_id):
            normalized = _normalize_record_for_export(record)
            if normalized is not None:
                records.append(normalized)
        html_source_path = Path(f"session-{source_session_id[:8]}.jsonl")
    elif args.source:
        trace_file = Path(args.source)
        if not trace_file.exists():
            print(f"Error: trace file not found: {trace_file}", file=sys.stderr)
            return 1
        records, compact_bundle = _load_records_from_text(trace_file.read_text(encoding="utf-8"))
        html_source_path = trace_file
    else:
        parser.error("provide a .jsonl trace file path or --session-id")

    # Determine format
    fmt = args.format
    if fmt is None:
        if args.output:
            suffix = args.output.suffix.lower()
            if suffix == ".json":
                fmt = "json"
            elif suffix in {".html", ".htm"}:
                fmt = "html"
            elif _compact_output_suffix(args.output):
                fmt = "compact"
            elif _is_prompt_markdown_output(args.output):
                fmt = "prompt-md"
            else:
                fmt = "markdown"
        else:
            fmt = "markdown"

    if not records:
        print("Error: no valid records found in trace file", file=sys.stderr)
        return 1

    if fmt != "compact":
        records.sort(key=_turn_sort_key)

    if fmt == "html":
        if html_source_path is None:
            print("Error: HTML export requires a JSONL source path", file=sys.stderr)
            return 1
        html_path = args.output or html_source_path.with_suffix(".html")
        if compact_bundle is not None:
            _generate_html_viewer_from_compact_bundle(
                compact_bundle,
                html_path,
                display_trace_path=html_source_path.absolute(),
                display_html_path=html_path.absolute(),
            )
        elif source_session_id:
            _generate_html_viewer_from_compact_bundle(
                build_compact_trace_bundle(records),
                html_path,
                display_trace_path=f"session:{source_session_id}",
                display_html_path=html_path.absolute(),
            )
        else:
            _generate_html_viewer_from_records(
                [
                    _normalize_record_for_viewer(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
                    for record in records
                ],
                html_path,
                display_trace_path=html_source_path.absolute(),
                display_html_path=html_path.absolute(),
            )
        if not html_path.exists():
            print("Error: failed to generate HTML viewer", file=sys.stderr)
            return 1
        print(f"Exported {len(records)} turns to {html_path}")
        return 0

    if fmt == "compact":
        if source_session_id and store is not None:
            output = store.export_compact(source_session_id)
        else:
            output = dump_compact_trace(records)
    elif fmt == "prompt-md":
        try:
            output = render_prompt_markdown(snapshot_from_records(records))
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
    elif fmt == "json":
        output = _export_json(_normalize_records_for_export(records))
    else:
        output = _export_markdown(_normalize_records_for_export(records))

    if args.output:
        args.output.write_text(output, encoding="utf-8")
        print(f"Exported {len(records)} turns to {args.output}")
    else:
        print(output)

    return 0


def _is_prompt_markdown_output(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith((".prompt.md", ".prompt.markdown", ".system.md", ".system.markdown"))


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
        usage = normalize_usage(resp_body.get("usage"))
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
