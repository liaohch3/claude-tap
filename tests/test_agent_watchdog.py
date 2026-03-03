#!/usr/bin/env python3
"""Unit tests for scripts/agent_watchdog.py decision logic."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

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
        elapsed_since_progress_minutes=31,
        human_verdict=None,
        session_running=True,
        timeout_minutes=15,
        stuck_minutes=30,
        bad_patterns=[r"error"],
    )

    assert decision.state == module.STATE_STUCK
    assert decision.reason == "stuck_timeout"


def test_bad_pattern_sets_running_bad() -> None:
    module = _load_module()

    decision = module.evaluate_decision(
        pane_tail="Traceback: boom",
        elapsed_since_progress_minutes=3,
        human_verdict=None,
        session_running=True,
        timeout_minutes=15,
        stuck_minutes=30,
        bad_patterns=[r"traceback"],
    )

    assert decision.state == module.STATE_RUNNING_BAD
    assert decision.action == "send_corrective"


def test_slow_progress_sets_running_bad() -> None:
    module = _load_module()

    decision = module.evaluate_decision(
        pane_tail="working",
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
        elapsed_since_progress_minutes=4,
        human_verdict=None,
        session_running=True,
        timeout_minutes=15,
        stuck_minutes=30,
        bad_patterns=[r"fatal"],
    )

    assert decision.state == module.STATE_RUNNING_GOOD
    assert decision.action == "noop"
