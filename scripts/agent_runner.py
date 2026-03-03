#!/usr/bin/env python3
"""Unified tmux wrapper for Codex and Claude agents."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

DEFAULT_PANE_LINES = 120
AGENT_CMDS = {
    "codex": "codex",
    "claude": "claude",
}


def _run(cmd: list[str], *, input_text: str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        input=input_text,
        text=True,
        capture_output=True,
        check=check,
    )


def _require_tmux() -> None:
    if not shutil_which("tmux"):
        raise SystemExit("tmux not found in PATH")


def shutil_which(binary: str) -> str | None:
    for path in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(path) / binary
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def deterministic_session_name(agent: str, task_id: str, workdir: str) -> str:
    safe_task = re.sub(r"[^a-zA-Z0-9_-]+", "-", task_id).strip("-").lower() or "task"
    workdir_hash = hashlib.sha1(Path(workdir).resolve().as_posix().encode("utf-8")).hexdigest()[:8]
    return f"agent_{agent}_{safe_task[:20]}_{workdir_hash}"


def session_exists(session_name: str) -> bool:
    proc = _run(["tmux", "has-session", "-t", session_name], check=False)
    return proc.returncode == 0


def send_text(session_name: str, text: str) -> None:
    _run(["tmux", "load-buffer", "-"], input_text=text)
    _run(["tmux", "paste-buffer", "-t", f"{session_name}:0.0"])
    _run(["tmux", "send-keys", "-t", session_name, "Enter"])


def start_session(args: argparse.Namespace) -> int:
    _require_tmux()
    session_name = args.session_name or deterministic_session_name(args.agent, args.task_id, args.workdir)
    workdir = str(Path(args.workdir).resolve())
    if session_exists(session_name):
        payload = {"session_name": session_name, "running": True, "status": "already_running"}
        print(json.dumps(payload))
        return 0

    agent_cmd = os.environ.get(f"AGENT_RUNNER_{args.agent.upper()}_CMD", AGENT_CMDS[args.agent])
    _run(["tmux", "new-session", "-d", "-s", session_name, "-c", workdir, agent_cmd])

    if args.prompt_file:
        prompt_text = Path(args.prompt_file).read_text(encoding="utf-8")
        if prompt_text.strip():
            send_text(session_name, prompt_text)
    if args.message and args.message.strip():
        send_text(session_name, args.message)

    payload = {
        "session_name": session_name,
        "running": True,
        "status": "started",
        "agent": args.agent,
        "task_id": args.task_id,
        "workdir": workdir,
    }
    print(json.dumps(payload))
    return 0


def status_session(args: argparse.Namespace) -> int:
    _require_tmux()
    session_name = args.session_name or deterministic_session_name(args.agent, args.task_id, args.workdir)
    running = session_exists(session_name)
    payload: dict[str, object] = {"session_name": session_name, "running": running}
    if running:
        pane = _run(
            ["tmux", "capture-pane", "-p", "-S", f"-{args.tail_lines}", "-t", f"{session_name}:0.0"],
            check=False,
        )
        payload["tail"] = pane.stdout
    print(json.dumps(payload))
    return 0


def send_session(args: argparse.Namespace) -> int:
    _require_tmux()
    session_name = args.session_name or deterministic_session_name(args.agent, args.task_id, args.workdir)
    if not session_exists(session_name):
        raise SystemExit(f"tmux session does not exist: {session_name}")

    text = args.message
    if args.prompt_file:
        text = Path(args.prompt_file).read_text(encoding="utf-8")
    if text is None:
        raise SystemExit("send requires either --message or --prompt-file")

    send_text(session_name, text)
    print(json.dumps({"session_name": session_name, "status": "sent"}))
    return 0


def stop_session(args: argparse.Namespace) -> int:
    _require_tmux()
    session_name = args.session_name or deterministic_session_name(args.agent, args.task_id, args.workdir)
    if not session_exists(session_name):
        print(json.dumps({"session_name": session_name, "status": "not_found", "running": False}))
        return 0

    _run(["tmux", "kill-session", "-t", session_name])
    print(json.dumps({"session_name": session_name, "status": "stopped", "running": False}))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser, *, include_agent: bool = True) -> None:
        if include_agent:
            p.add_argument("--agent", choices=("codex", "claude"), required=True)
        p.add_argument("--task-id", required=True)
        p.add_argument("--workdir", required=True)
        p.add_argument("--session-name")

    p_start = sub.add_parser("start")
    add_common(p_start)
    p_start.add_argument("--prompt-file")
    p_start.add_argument("--message")
    p_start.set_defaults(func=start_session)

    p_status = sub.add_parser("status")
    add_common(p_status)
    p_status.add_argument("--tail-lines", type=int, default=DEFAULT_PANE_LINES)
    p_status.set_defaults(func=status_session)

    p_send = sub.add_parser("send")
    add_common(p_send)
    p_send.add_argument("--prompt-file")
    p_send.add_argument("--message")
    p_send.set_defaults(func=send_session)

    p_stop = sub.add_parser("stop")
    add_common(p_stop)
    p_stop.set_defaults(func=stop_session)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
