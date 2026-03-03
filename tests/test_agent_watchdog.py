#!/usr/bin/env python3
"""Unit tests for scripts/agent_watchdog.py decision logic and tick behavior."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "agent_watchdog.py"
MODULE_NAME = "agent_watchdog"


def _load_module():
    spec = importlib.util.spec_from_file_location(MODULE_NAME, SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


def test_human_approve_sets_done_pass() -> None:
    module = _load_module()

    decision = module.evaluate_decision(
        pane_tail="all good",
        marker_event=None,
        matched_new_bad_pattern=None,
        elapsed_since_progress_minutes=2,
        human_verdict="approve",
        session_running=True,
        timeout_minutes=15,
        stuck_minutes=30,
        bad_patterns=[r"error"],
    )

    assert decision.state == module.STATE_DONE_PASS
    assert decision.action == "noop"


def test_human_reject_sets_done_fail_and_retry() -> None:
    module = _load_module()

    decision = module.evaluate_decision(
        pane_tail="needs fixes",
        marker_event=None,
        matched_new_bad_pattern=None,
        elapsed_since_progress_minutes=1,
        human_verdict="reject",
        session_running=True,
        timeout_minutes=15,
        stuck_minutes=30,
        bad_patterns=[r"error"],
    )

    assert decision.state == module.STATE_DONE_FAIL
    assert decision.action == "send_retry"


def test_missing_session_is_stuck() -> None:
    module = _load_module()

    decision = module.evaluate_decision(
        pane_tail="",
        marker_event=None,
        matched_new_bad_pattern=None,
        elapsed_since_progress_minutes=1,
        human_verdict=None,
        session_running=False,
        timeout_minutes=15,
        stuck_minutes=30,
        bad_patterns=[r"error"],
    )

    assert decision.state == module.STATE_STUCK
    assert decision.action == "restart"


def test_stuck_timeout_is_stuck() -> None:
    module = _load_module()

    decision = module.evaluate_decision(
        pane_tail="still spinning",
        marker_event=None,
        matched_new_bad_pattern=None,
        elapsed_since_progress_minutes=31,
        human_verdict=None,
        session_running=True,
        timeout_minutes=15,
        stuck_minutes=30,
        bad_patterns=[r"error"],
    )

    assert decision.state == module.STATE_STUCK
    assert decision.reason == "stuck_timeout"


def test_new_bad_pattern_sets_running_bad() -> None:
    module = _load_module()

    decision = module.evaluate_decision(
        pane_tail="Traceback: boom",
        marker_event=None,
        matched_new_bad_pattern=r"traceback",
        elapsed_since_progress_minutes=3,
        human_verdict=None,
        session_running=True,
        timeout_minutes=15,
        stuck_minutes=30,
        bad_patterns=[r"traceback"],
    )

    assert decision.state == module.STATE_RUNNING_BAD
    assert decision.action == "send_corrective"


def test_progress_marker_prioritizes_running_good_over_regex() -> None:
    module = _load_module()
    marker_event = module.MarkerEvent(module.EVENT_MARKER_PROGRESS, "editing file")

    decision = module.evaluate_decision(
        pane_tail="[WD_PROGRESS] editing file\nTraceback: old issue",
        marker_event=marker_event,
        matched_new_bad_pattern=r"traceback",
        elapsed_since_progress_minutes=3,
        human_verdict=None,
        session_running=True,
        timeout_minutes=15,
        stuck_minutes=30,
        bad_patterns=[r"traceback"],
    )

    assert decision.state == module.STATE_RUNNING_GOOD
    assert decision.action == "noop"
    assert decision.reason == f"marker:{module.EVENT_MARKER_PROGRESS}"


def test_slow_progress_sets_running_bad() -> None:
    module = _load_module()

    decision = module.evaluate_decision(
        pane_tail="working",
        marker_event=None,
        matched_new_bad_pattern=None,
        elapsed_since_progress_minutes=16,
        human_verdict=None,
        session_running=True,
        timeout_minutes=15,
        stuck_minutes=30,
        bad_patterns=[r"error"],
    )

    assert decision.state == module.STATE_RUNNING_BAD
    assert decision.reason == "slow_progress"


def test_healthy_state_is_running_good() -> None:
    module = _load_module()

    decision = module.evaluate_decision(
        pane_tail="editing files",
        marker_event=None,
        matched_new_bad_pattern=None,
        elapsed_since_progress_minutes=4,
        human_verdict=None,
        session_running=True,
        timeout_minutes=15,
        stuck_minutes=30,
        bad_patterns=[r"fatal"],
    )

    assert decision.state == module.STATE_RUNNING_GOOD
    assert decision.action == "noop"


def _init_task(module, tmp_path: Path, task_id: str) -> None:
    args = argparse.Namespace(
        task_id=task_id,
        agent="codex",
        workdir=str(tmp_path),
        session_name=f"sess-{task_id}",
        prompt_file=None,
        timeout_minutes=60,
        stuck_minutes=120,
        action_cooldown_minutes=5,
        bad_pattern=[r"traceback", r"error:"],
    )
    assert module.cmd_init(args) == 0


def test_tick_stale_bad_log_does_not_repeat_corrective(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    module = _load_module()
    _init_task(module, tmp_path, "stale-bad")

    statuses = iter(
        [
            {"running": True, "tail": "Traceback: stale failure\n"},
            {"running": True, "tail": "Traceback: stale failure\n"},
        ]
    )
    sent: list[str] = []
    monkeypatch.setattr(module, "_capture_status", lambda state, tail_lines: next(statuses))
    monkeypatch.setattr(module, "_send_prompt", lambda state, prompt_text: sent.append(prompt_text))
    monkeypatch.setattr(module, "_stop_agent", lambda state: None)
    monkeypatch.setattr(module, "_start_agent", lambda state, resume_message=None: None)

    tick_args = argparse.Namespace(task_id="stale-bad", tail_lines=120, once=True)
    assert module.cmd_tick(tick_args) == 0
    first_state = module.load_state("stale-bad")
    assert first_state["last_action"]["type"] == "send_corrective"

    assert module.cmd_tick(tick_args) == 0
    second_state = module.load_state("stale-bad")
    assert second_state["last_action"]["type"] == "noop"
    assert len(sent) == 1


def test_tick_new_blocker_marker_triggers_corrective(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    module = _load_module()
    _init_task(module, tmp_path, "new-blocker")

    statuses = iter(
        [
            {"running": True, "tail": "working\n"},
            {"running": True, "tail": "working\n[WD_BLOCKER] waiting for token\n"},
        ]
    )
    sent: list[str] = []
    monkeypatch.setattr(module, "_capture_status", lambda state, tail_lines: next(statuses))
    monkeypatch.setattr(module, "_send_prompt", lambda state, prompt_text: sent.append(prompt_text))
    monkeypatch.setattr(module, "_stop_agent", lambda state: None)
    monkeypatch.setattr(module, "_start_agent", lambda state, resume_message=None: None)

    tick_args = argparse.Namespace(task_id="new-blocker", tail_lines=120, once=True)
    assert module.cmd_tick(tick_args) == 0
    assert module.cmd_tick(tick_args) == 0
    state = module.load_state("new-blocker")
    assert state["last_action"]["type"] == "send_corrective"
    assert len(sent) == 1


def test_tick_done_marker_plus_reject_verdict_triggers_retry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    module = _load_module()
    _init_task(module, tmp_path, "done-reject")

    statuses = iter([{"running": True, "tail": "[WD_DONE] ready_for_review\n"}])
    sent: list[str] = []
    monkeypatch.setattr(module, "_capture_status", lambda state, tail_lines: next(statuses))
    monkeypatch.setattr(module, "_send_prompt", lambda state, prompt_text: sent.append(prompt_text))
    monkeypatch.setattr(module, "_stop_agent", lambda state: None)
    monkeypatch.setattr(module, "_start_agent", lambda state, resume_message=None: None)

    verdict_args = argparse.Namespace(task_id="done-reject", verdict="reject")
    assert module.cmd_verdict(verdict_args) == 0

    tick_args = argparse.Namespace(task_id="done-reject", tail_lines=120, once=True)
    assert module.cmd_tick(tick_args) == 0
    state = module.load_state("done-reject")
    assert state["state"] == module.STATE_DONE_FAIL
    assert state["last_action"]["type"] == "send_retry"
    assert state["human_verdict"] is None
    assert len(sent) == 1
