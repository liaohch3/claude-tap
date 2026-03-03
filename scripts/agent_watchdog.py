#!/usr/bin/env python3
"""State-machine watchdog for tmux-based coding agent tasks."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

STATE_RUNNING_GOOD = "RUNNING_GOOD"
STATE_RUNNING_BAD = "RUNNING_BAD"
STATE_DONE_PASS = "DONE_PASS"
STATE_DONE_FAIL = "DONE_FAIL"
STATE_STUCK = "STUCK"
ALL_STATES = {STATE_RUNNING_GOOD, STATE_RUNNING_BAD, STATE_DONE_PASS, STATE_DONE_FAIL, STATE_STUCK}

DEFAULT_TIMEOUT_MINUTES = 15
DEFAULT_STUCK_MINUTES = 30
DEFAULT_ACTION_COOLDOWN_MINUTES = 5
DEFAULT_BAD_PATTERNS = [
    r"permission denied",
    r"sandbox",
    r"rate limit",
    r"traceback",
    r"error:",
]
EVENT_MARKER_PROGRESS = "WD_PROGRESS"
EVENT_MARKER_BLOCKER = "WD_BLOCKER"
EVENT_MARKER_DONE = "WD_DONE"
EVENT_MARKERS = (EVENT_MARKER_PROGRESS, EVENT_MARKER_BLOCKER, EVENT_MARKER_DONE)
EVENT_MARKER_REGEX = re.compile(r"\[(WD_PROGRESS|WD_BLOCKER|WD_DONE)\]\s*(.*)")

CORRECTIVE_TEMPLATE = Path("prompts/agent-templates/corrective.md")
RETRY_TEMPLATE = Path("prompts/agent-templates/retry-after-review-fail.md")
RESUME_TEMPLATE = Path("prompts/agent-templates/resume-after-stuck.md")


@dataclass(frozen=True)
class Decision:
    state: str
    reason: str
    action: str


@dataclass(frozen=True)
class MarkerEvent:
    marker: str
    detail: str


def now_utc_iso() -> str:
    return dt.datetime.now(tz=dt.timezone.utc).replace(microsecond=0).isoformat()


def parse_utc(iso_text: str | None) -> dt.datetime | None:
    if not iso_text:
        return None
    return dt.datetime.fromisoformat(iso_text)


def elapsed_minutes(since_iso: str | None, now: dt.datetime) -> float:
    since = parse_utc(since_iso)
    if since is None:
        return 10**9
    return (now - since).total_seconds() / 60.0


def _matched_bad_pattern(pane_tail: str, bad_patterns: list[str]) -> str | None:
    for pattern in bad_patterns:
        if re.search(pattern, pane_tail, flags=re.IGNORECASE):
            return pattern
    return None


def _extract_new_tail_segment(previous_tail: str, current_tail: str) -> str:
    if not previous_tail:
        return current_tail
    if current_tail == previous_tail:
        return ""

    prev_lines = previous_tail.splitlines()
    curr_lines = current_tail.splitlines()
    if not prev_lines:
        return current_tail
    if curr_lines == prev_lines:
        return ""

    prev_count = len(prev_lines)
    curr_count = len(curr_lines)
    if curr_count >= prev_count:
        for start in range(curr_count - prev_count + 1):
            if curr_lines[start : start + prev_count] == prev_lines:
                return "\n".join(curr_lines[start + prev_count :])

    max_overlap = min(prev_count, curr_count)
    for overlap in range(max_overlap, 0, -1):
        if prev_lines[-overlap:] == curr_lines[:overlap]:
            return "\n".join(curr_lines[overlap:])
    return current_tail


def _extract_latest_marker_event(text: str) -> MarkerEvent | None:
    latest: MarkerEvent | None = None
    for line in text.splitlines():
        match = EVENT_MARKER_REGEX.search(line)
        if match:
            latest = MarkerEvent(marker=match.group(1), detail=match.group(2).strip())
    return latest


def evaluate_decision(
    *,
    pane_tail: str,
    marker_event: MarkerEvent | None,
    matched_new_bad_pattern: str | None,
    elapsed_since_progress_minutes: float,
    human_verdict: str | None,
    session_running: bool,
    timeout_minutes: int,
    stuck_minutes: int,
    bad_patterns: list[str],
) -> Decision:
    verdict = (human_verdict or "").strip().lower()
    if verdict == "approve":
        return Decision(state=STATE_DONE_PASS, reason="human_approved", action="noop")
    if verdict == "reject":
        return Decision(state=STATE_DONE_FAIL, reason="human_rejected", action="send_retry")

    if not session_running:
        return Decision(state=STATE_STUCK, reason="session_missing", action="restart")

    if elapsed_since_progress_minutes >= stuck_minutes:
        return Decision(state=STATE_STUCK, reason="stuck_timeout", action="restart")

    if marker_event is not None:
        if marker_event.marker == EVENT_MARKER_BLOCKER:
            return Decision(
                state=STATE_RUNNING_BAD,
                reason=f"marker:{EVENT_MARKER_BLOCKER}",
                action="send_corrective",
            )
        if marker_event.marker == EVENT_MARKER_PROGRESS:
            return Decision(state=STATE_RUNNING_GOOD, reason=f"marker:{EVENT_MARKER_PROGRESS}", action="noop")
        if marker_event.marker == EVENT_MARKER_DONE:
            return Decision(state=STATE_RUNNING_GOOD, reason=f"marker:{EVENT_MARKER_DONE}", action="noop")

    if matched_new_bad_pattern is not None:
        return Decision(
            state=STATE_RUNNING_BAD,
            reason=f"bad_pattern:{matched_new_bad_pattern}",
            action="send_corrective",
        )

    if elapsed_since_progress_minutes >= timeout_minutes:
        return Decision(state=STATE_RUNNING_BAD, reason="slow_progress", action="send_corrective")

    return Decision(state=STATE_RUNNING_GOOD, reason="healthy", action="noop")


def _state_path(task_id: str) -> Path:
    return Path("memory") / "agent-tasks" / f"{task_id}.json"


def deterministic_session_name(agent: str, task_id: str, workdir: str) -> str:
    safe_task = re.sub(r"[^a-zA-Z0-9_-]+", "-", task_id).strip("-").lower() or "task"
    workdir_hash = hashlib.sha1(Path(workdir).resolve().as_posix().encode("utf-8")).hexdigest()[:8]
    return f"agent_{agent}_{safe_task[:20]}_{workdir_hash}"


def load_state(task_id: str) -> dict:
    path = _state_path(task_id)
    if not path.exists():
        raise SystemExit(f"state file does not exist: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(task_id: str, state: dict) -> None:
    path = _state_path(task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _runner_cmd(*args: str) -> list[str]:
    return [sys.executable, "scripts/agent_runner.py", *args]


def _run_runner(*args: str, capture: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(_runner_cmd(*args), check=True, text=True, capture_output=capture)


def _render_template(template_path: Path, context: dict[str, str]) -> str:
    template = template_path.read_text(encoding="utf-8")
    return template.format_map(context)


def _tail_fingerprint(pane_tail: str) -> str:
    return hashlib.sha1(pane_tail.encode("utf-8")).hexdigest()


def _action_allowed(state: dict, action: str, now: dt.datetime) -> bool:
    if action == "noop":
        return False
    last_action = state.get("last_action", {})
    if last_action.get("type") != action:
        return True
    last_at = parse_utc(last_action.get("at"))
    if last_at is None:
        return True
    cooldown = state.get("config", {}).get("action_cooldown_minutes", DEFAULT_ACTION_COOLDOWN_MINUTES)
    return (now - last_at).total_seconds() / 60.0 >= cooldown


def _capture_status(state: dict, *, tail_lines: int) -> dict:
    proc = _run_runner(
        "status",
        "--agent",
        state["agent"],
        "--task-id",
        state["task_id"],
        "--workdir",
        state["workdir"],
        "--session-name",
        state["session_name"],
        "--tail-lines",
        str(tail_lines),
    )
    return json.loads(proc.stdout)


def _send_prompt(state: dict, prompt_text: str) -> None:
    _run_runner(
        "send",
        "--agent",
        state["agent"],
        "--task-id",
        state["task_id"],
        "--workdir",
        state["workdir"],
        "--session-name",
        state["session_name"],
        "--message",
        prompt_text,
        capture=False,
    )


def _start_agent(state: dict, resume_message: str | None = None) -> None:
    args = [
        "start",
        "--agent",
        state["agent"],
        "--task-id",
        state["task_id"],
        "--workdir",
        state["workdir"],
        "--session-name",
        state["session_name"],
    ]
    if state.get("prompt_file"):
        args.extend(["--prompt-file", state["prompt_file"]])
    if resume_message:
        args.extend(["--message", resume_message])
    _run_runner(*args, capture=False)


def _stop_agent(state: dict) -> None:
    _run_runner(
        "stop",
        "--agent",
        state["agent"],
        "--task-id",
        state["task_id"],
        "--workdir",
        state["workdir"],
        "--session-name",
        state["session_name"],
        capture=False,
    )


def cmd_init(args: argparse.Namespace) -> int:
    now = now_utc_iso()
    bad_patterns = args.bad_pattern if args.bad_pattern else DEFAULT_BAD_PATTERNS
    workdir = str(Path(args.workdir).resolve())
    session_name = args.session_name or deterministic_session_name(args.agent, args.task_id, workdir)
    state = {
        "task_id": args.task_id,
        "agent": args.agent,
        "workdir": workdir,
        "session_name": session_name,
        "prompt_file": args.prompt_file,
        "state": STATE_RUNNING_GOOD,
        "human_verdict": None,
        "created_at": now,
        "updated_at": now,
        "last_progress_at": now,
        "last_seen_tail_hash": None,
        "last_seen_line_count_marker": 0,
        "last_seen_tail_text": "",
        # Backward compatibility for older state files.
        "last_pane_fingerprint": None,
        "last_action": {"type": None, "at": None, "detail": None},
        "config": {
            "timeout_minutes": args.timeout_minutes,
            "stuck_minutes": args.stuck_minutes,
            "action_cooldown_minutes": args.action_cooldown_minutes,
            "bad_patterns": bad_patterns,
        },
        "history": [{"at": now, "state": STATE_RUNNING_GOOD, "reason": "initialized", "action": "noop"}],
    }
    save_state(args.task_id, state)
    print(json.dumps(state, indent=2, sort_keys=True))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    state = load_state(args.task_id)
    print(json.dumps(state, indent=2, sort_keys=True))
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    state = load_state(args.task_id)
    _start_agent(state)
    print(json.dumps({"task_id": args.task_id, "status": "started", "session_name": state["session_name"]}))
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    state = load_state(args.task_id)
    _stop_agent(state)
    print(json.dumps({"task_id": args.task_id, "status": "stopped", "session_name": state["session_name"]}))
    return 0


def cmd_verdict(args: argparse.Namespace) -> int:
    state = load_state(args.task_id)
    state["human_verdict"] = args.verdict
    state["updated_at"] = now_utc_iso()
    save_state(args.task_id, state)
    print(json.dumps({"task_id": args.task_id, "human_verdict": args.verdict}))
    return 0


def _append_history(state: dict, *, at: str, decision: Decision) -> None:
    history = state.setdefault("history", [])
    history.append(
        {
            "at": at,
            "state": decision.state,
            "reason": decision.reason,
            "action": decision.action,
        }
    )


def cmd_tick(args: argparse.Namespace) -> int:
    state = load_state(args.task_id)
    if state.get("state") not in ALL_STATES:
        raise SystemExit(f"invalid state: {state.get('state')}")

    now = dt.datetime.now(tz=dt.timezone.utc)
    status = _capture_status(state, tail_lines=args.tail_lines)
    pane_tail = str(status.get("tail", ""))
    pane_fingerprint = _tail_fingerprint(pane_tail)
    pane_line_count = len(pane_tail.splitlines())
    previous_fingerprint = state.get("last_seen_tail_hash") or state.get("last_pane_fingerprint")
    previous_line_count = int(state.get("last_seen_line_count_marker") or 0)
    previous_tail = str(state.get("last_seen_tail_text", ""))
    tail_changed = pane_fingerprint != previous_fingerprint or pane_line_count != previous_line_count
    new_tail_segment = _extract_new_tail_segment(previous_tail, pane_tail) if tail_changed else ""

    if tail_changed:
        state["last_progress_at"] = now.isoformat()
    state["last_seen_tail_hash"] = pane_fingerprint
    state["last_seen_line_count_marker"] = pane_line_count
    state["last_seen_tail_text"] = pane_tail
    # Backward compatibility for older state readers.
    state["last_pane_fingerprint"] = pane_fingerprint

    timeout_minutes = int(state.get("config", {}).get("timeout_minutes", DEFAULT_TIMEOUT_MINUTES))
    stuck_minutes = int(state.get("config", {}).get("stuck_minutes", DEFAULT_STUCK_MINUTES))
    bad_patterns = list(state.get("config", {}).get("bad_patterns", DEFAULT_BAD_PATTERNS))
    marker_event = _extract_latest_marker_event(new_tail_segment)
    matched_new_bad_pattern = _matched_bad_pattern(new_tail_segment, bad_patterns)

    elapsed = elapsed_minutes(state.get("last_progress_at"), now)
    decision = evaluate_decision(
        pane_tail=pane_tail,
        marker_event=marker_event,
        matched_new_bad_pattern=matched_new_bad_pattern,
        elapsed_since_progress_minutes=elapsed,
        human_verdict=state.get("human_verdict"),
        session_running=bool(status.get("running", False)),
        timeout_minutes=timeout_minutes,
        stuck_minutes=stuck_minutes,
        bad_patterns=bad_patterns,
    )

    context = {
        "task_id": state["task_id"],
        "agent": state["agent"],
        "state": decision.state,
        "reason": decision.reason,
        "elapsed_minutes": f"{elapsed:.1f}",
        "pane_tail": pane_tail[-4000:],
        "new_tail_segment": new_tail_segment[-4000:],
        "event_marker": marker_event.marker if marker_event else "",
        "event_detail": marker_event.detail if marker_event else "",
    }

    action_taken = "noop"
    action_detail = None
    if _action_allowed(state, decision.action, now):
        if decision.action == "send_corrective":
            prompt = _render_template(CORRECTIVE_TEMPLATE, context)
            _send_prompt(state, prompt)
            action_taken = decision.action
            action_detail = "corrective_sent"
        elif decision.action == "send_retry":
            prompt = _render_template(RETRY_TEMPLATE, context)
            _send_prompt(state, prompt)
            action_taken = decision.action
            action_detail = "retry_sent"
            state["human_verdict"] = None
        elif decision.action == "restart":
            prompt = _render_template(RESUME_TEMPLATE, context)
            _stop_agent(state)
            _start_agent(state, resume_message=prompt)
            action_taken = decision.action
            action_detail = "session_restarted"

    now_iso = now.isoformat()
    state["state"] = decision.state
    state["updated_at"] = now_iso
    state["last_action"] = {"type": action_taken, "at": now_iso, "detail": action_detail}
    _append_history(state, at=now_iso, decision=Decision(decision.state, decision.reason, action_taken))
    save_state(args.task_id, state)

    output = {
        "task_id": args.task_id,
        "state": state["state"],
        "reason": decision.reason,
        "action": action_taken,
        "session_running": bool(status.get("running", False)),
        "elapsed_since_progress_minutes": round(elapsed, 2),
        "new_tail_segment_lines": len(new_tail_segment.splitlines()),
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


def _contains_arg_value(line: str, arg_name: str, value: str) -> bool:
    pattern = rf"{re.escape(arg_name)}(?:[=\s]+){re.escape(value)}(?:\s|$)"
    return re.search(pattern, line) is not None


def cmd_check_delivery(args: argparse.Namespace) -> int:
    proc = subprocess.run(["openclaw", "cron", "list"], check=False, text=True, capture_output=True)
    if proc.returncode != 0:
        raise SystemExit(f"openclaw cron list failed: {proc.stderr.strip() or 'unknown error'}")

    matched_line: str | None = None
    for raw_line in proc.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if args.task_id and args.task_id not in line:
            continue
        if _contains_arg_value(line, "--channel", args.channel) and _contains_arg_value(line, "--to", args.to):
            matched_line = line
            break

    if matched_line is None:
        output = {
            "ok": False,
            "reason": "delivery_target_missing",
            "task_id_filter": args.task_id,
            "required_channel": args.channel,
            "required_to": args.to,
        }
        print(json.dumps(output, indent=2, sort_keys=True))
        return 2

    output = {
        "ok": True,
        "reason": "delivery_target_found",
        "task_id_filter": args.task_id,
        "required_channel": args.channel,
        "required_to": args.to,
        "matched_cron_entry": matched_line,
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init")
    p_init.add_argument("--task-id", required=True)
    p_init.add_argument("--agent", choices=("codex", "claude"), required=True)
    p_init.add_argument("--workdir", required=True)
    p_init.add_argument("--session-name")
    p_init.add_argument("--prompt-file")
    p_init.add_argument("--timeout-minutes", type=int, default=DEFAULT_TIMEOUT_MINUTES)
    p_init.add_argument("--stuck-minutes", type=int, default=DEFAULT_STUCK_MINUTES)
    p_init.add_argument("--action-cooldown-minutes", type=int, default=DEFAULT_ACTION_COOLDOWN_MINUTES)
    p_init.add_argument("--bad-pattern", action="append")
    p_init.set_defaults(func=cmd_init)

    p_status = sub.add_parser("status")
    p_status.add_argument("--task-id", required=True)
    p_status.set_defaults(func=cmd_status)

    p_start = sub.add_parser("start")
    p_start.add_argument("--task-id", required=True)
    p_start.set_defaults(func=cmd_start)

    p_stop = sub.add_parser("stop")
    p_stop.add_argument("--task-id", required=True)
    p_stop.set_defaults(func=cmd_stop)

    p_verdict = sub.add_parser("verdict")
    p_verdict.add_argument("--task-id", required=True)
    p_verdict.add_argument("--verdict", choices=("approve", "reject"), required=True)
    p_verdict.set_defaults(func=cmd_verdict)

    p_tick = sub.add_parser("tick")
    p_tick.add_argument("--task-id", required=True)
    p_tick.add_argument("--tail-lines", type=int, default=120)
    p_tick.add_argument("--once", action="store_true", help="Explicit one-shot tick for cron/manual invocation.")
    p_tick.set_defaults(func=cmd_tick)

    p_check_delivery = sub.add_parser("check-delivery")
    p_check_delivery.add_argument("--channel", required=True)
    p_check_delivery.add_argument("--to", required=True)
    p_check_delivery.add_argument("--task-id")
    p_check_delivery.set_defaults(func=cmd_check_delivery)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
