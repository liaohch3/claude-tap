"""CLI entry for ``claude-tap summary``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from claude_tap.export import _load_records_from_text, _normalize_record_for_export
from claude_tap.session_briefing import summarize_session


def summary_main(argv: list[str] | None = None) -> int:
    """Print the session briefing JSON for a trace file or stored session."""
    parser = argparse.ArgumentParser(
        prog="claude-tap summary",
        description="Print a session briefing computed from captured metadata.",
    )
    parser.add_argument("source", type=str, nargs="?", help="Path to a .jsonl / compact trace, or a SQLite session id")
    parser.add_argument("--session-id", dest="session_id", help="Summarize a stored SQLite session by id")
    args = parser.parse_args(argv)

    records: list[dict] = []
    source_session_id = args.session_id

    if source_session_id is None and args.source:
        trace_file = Path(args.source)
        if not trace_file.exists():
            from claude_tap.trace_store import get_trace_store

            store = get_trace_store()
            if store.load_session_row(args.source) is not None:
                source_session_id = args.source

    if source_session_id:
        from claude_tap.trace_store import get_trace_store

        store = get_trace_store()
        if store.load_session_row(source_session_id) is None:
            print(f"Error: session not found: {source_session_id}", file=sys.stderr)
            return 1
        for record in store.load_records(source_session_id):
            normalized = _normalize_record_for_export(record)
            if normalized is not None:
                records.append(normalized)
    elif args.source:
        trace_file = Path(args.source)
        if not trace_file.exists():
            print(f"Error: trace file not found: {trace_file}", file=sys.stderr)
            return 1
        records, _compact = _load_records_from_text(trace_file.read_text(encoding="utf-8"))
    else:
        parser.error("provide a .jsonl trace file path or --session-id")

    if not records:
        print("Error: no valid records found in trace file", file=sys.stderr)
        return 1

    print(json.dumps(summarize_session(records), ensure_ascii=False, indent=2))
    return 0
