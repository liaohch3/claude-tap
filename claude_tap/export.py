"""Export trace JSONL files to Markdown or JSON format."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def export_main(argv: list[str] | None = None) -> int:
    """Entry point for the export subcommand."""
    parser = argparse.ArgumentParser(
        prog="claude-tap export",
        description="Export a trace JSONL file to Markdown or JSON.",
    )
    parser.add_argument("trace_file", type=Path, help="Path to the .jsonl trace file")
    parser.add_argument("-o", "--output", type=Path, help="Output file path (default: stdout)")
    parser.add_argument(
        "--format",
        choices=["markdown", "json"],
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
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    if not records:
        print("Error: no valid records found in trace file", file=sys.stderr)
        return 1

    # Sort by turn
    records.sort(key=lambda r: r.get("turn", 0))

    # Determine format
    fmt = args.format
    if fmt is None:
        if args.output and args.output.suffix == ".json":
            fmt = "json"
        else:
            fmt = "markdown"

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
        usage = r.get("response", {}).get("body", {}).get("usage", {})
        total_input += usage.get("input_tokens", 0)
        total_output += usage.get("output_tokens", 0)
        total_cache_read += usage.get("cache_read_input_tokens", 0)
        total_cache_create += usage.get("cache_creation_input_tokens", 0)
        model = r.get("request", {}).get("body", {}).get("model", "")
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
        req_body = r.get("request", {}).get("body", {})
        resp_body = r.get("response", {}).get("body", {})
        model = req_body.get("model", "unknown")
        duration = r.get("duration_ms", 0)

        lines.append(f"---\n\n## Turn {turn}\n")
        lines.append(f"**Model**: `{model}` | **Duration**: {duration}ms\n")

        # User messages (last message from request)
        messages = req_body.get("messages", [])
        if messages:
            last_msg = messages[-1]
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
        if resp_content:
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
        usage = resp_body.get("usage", {})
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
        req_body = r.get("request", {}).get("body", {})
        resp_body = r.get("response", {}).get("body", {})

        entry = {
            "turn": r.get("turn"),
            "timestamp": r.get("timestamp"),
            "duration_ms": r.get("duration_ms"),
            "model": req_body.get("model"),
            "messages": req_body.get("messages", []),
            "response": {
                "content": resp_body.get("content", []),
                "usage": resp_body.get("usage", {}),
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
