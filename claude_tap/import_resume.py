"""Install a transplanted conversation as a resumable Claude Code session."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from claude_tap.session_transplant import (
    detect_claude_version,
    install_resume_session,
    parse_jsonl_conversation,
)


def _source_cwds(text: str) -> set[str]:
    found: set[str] = set()
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and isinstance(event.get("cwd"), str):
            found.add(event["cwd"])
    return found


def import_resume_main(argv: list[str] | None = None) -> int:
    """Entry point for the import-resume subcommand."""
    parser = argparse.ArgumentParser(
        prog="claude-tap import-resume",
        description="Install a transplant/session JSONL into Claude Code's project store so it can be resumed.",
    )
    parser.add_argument("source", type=Path, help="Path to a claude-resume export or a Claude Code session JSONL")
    parser.add_argument("--cwd", default=None, help="Target project directory to resume in (default: current dir)")
    parser.add_argument("--git-branch", default="", help="Git branch to stamp on the session")
    parser.add_argument("--session-id", default=None, help="Force a specific session id (default: new random uuid)")
    parser.add_argument("--home", type=Path, default=None, help="Override the Claude home dir (default: ~/.claude)")
    args = parser.parse_args(argv)

    if not args.source.exists():
        print(f"Error: source not found: {args.source}", file=sys.stderr)
        return 1

    text = args.source.read_text(encoding="utf-8")
    messages = parse_jsonl_conversation(text)
    if not messages:
        print("Error: no user/assistant messages found in source", file=sys.stderr)
        return 1

    target_cwd = os.path.abspath(args.cwd) if args.cwd else os.getcwd()
    source_cwds = {c for c in _source_cwds(text) if c}
    target_key = os.path.normcase(os.path.normpath(target_cwd))
    source_keys = {os.path.normcase(os.path.normpath(c)) for c in source_cwds}
    if source_cwds and target_key not in source_keys:
        origin = ", ".join(sorted(source_cwds))
        print(
            f"Warning: source was captured under {origin}; resuming in {target_cwd}. "
            "File paths in the conversation may not match this machine.",
            file=sys.stderr,
        )

    installed = install_resume_session(
        messages,
        target_cwd,
        home=args.home,
        version=detect_claude_version(),
        git_branch=args.git_branch,
        session_id=args.session_id,
    )

    print(f"Installed {installed.message_count} messages -> {installed.path}")
    print(f"Resume with:\n  cd {target_cwd} && {installed.resume_command}")
    return 0
