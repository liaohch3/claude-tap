#!/usr/bin/env python3
"""Unit tests for scripts/check_legibility.py."""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "check_legibility.py"
MODULE_NAME = "check_legibility"


def _load_module():
    spec = importlib.util.spec_from_file_location(MODULE_NAME, SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _base_repo(tmp_path: Path, *, last_reviewed: str = "2026-03-01", plan_status: str = "active") -> Path:
    _write(
        tmp_path / "docs" / "standards" / "policy.md",
        (f"---\nowner: docs-team\nlast_reviewed: {last_reviewed}\nsource_of_truth: AGENTS.md\n---\n\n# Policy\n"),
    )
    _write(
        tmp_path / "docs" / "architecture" / "manifest.yaml",
        "expected_paths:\n  - docs/standards/policy.md\n  - docs/plans/plan.md\n",
    )
    _write(
        tmp_path / "docs" / "plans" / "plan.md",
        f"---\nstatus: {plan_status}\n---\n\n# Plan\n\nNo TODO items.\n",
    )
    return tmp_path


def test_run_checks_happy_path(tmp_path: Path) -> None:
    module = _load_module()
    repo_root = _base_repo(tmp_path)

    result = module.run_checks(
        repo_root,
        freshness_days=60,
        strict_freshness=False,
        today=dt.date(2026, 3, 3),
    )

    assert result.failures == []
    assert result.warnings == []


def test_stale_standards_is_warning_in_mvp_mode(tmp_path: Path) -> None:
    module = _load_module()
    repo_root = _base_repo(tmp_path, last_reviewed="2025-01-01")

    result = module.run_checks(
        repo_root,
        freshness_days=60,
        strict_freshness=False,
        today=dt.date(2026, 3, 3),
    )

    assert result.failures == []
    assert any("stale" in warning for warning in result.warnings)


def test_stale_standards_fails_in_strict_mode(tmp_path: Path) -> None:
    module = _load_module()
    repo_root = _base_repo(tmp_path, last_reviewed="2025-01-01")

    result = module.run_checks(
        repo_root,
        freshness_days=60,
        strict_freshness=True,
        today=dt.date(2026, 3, 3),
    )

    assert result.warnings == []
    assert any("stale" in failure for failure in result.failures)


def test_missing_manifest_path_fails(tmp_path: Path) -> None:
    module = _load_module()
    repo_root = _base_repo(tmp_path)
    _write(
        repo_root / "docs" / "architecture" / "manifest.yaml",
        "expected_paths:\n  - docs/standards/policy.md\n  - docs/does-not-exist.md\n",
    )

    result = module.run_checks(
        repo_root,
        freshness_days=60,
        strict_freshness=False,
        today=dt.date(2026, 3, 3),
    )

    assert any("expected path missing" in failure for failure in result.failures)


def test_completed_plan_with_unchecked_todo_fails(tmp_path: Path) -> None:
    module = _load_module()
    repo_root = _base_repo(tmp_path, plan_status="completed")
    _write(
        repo_root / "docs" / "plans" / "plan.md",
        "---\nstatus: completed\n---\n\n# Plan\n\n- [ ] unresolved item\n",
    )

    result = module.run_checks(
        repo_root,
        freshness_days=60,
        strict_freshness=False,
        today=dt.date(2026, 3, 3),
    )

    assert any("unchecked TODO" in failure for failure in result.failures)
