"""Regression tests for the auto-release workflow shell contract."""

from __future__ import annotations

from pathlib import Path

WORKFLOW_PATH = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "auto-release.yml"


def _workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def test_release_pr_body_satisfies_policy_sections() -> None:
    workflow = _workflow_text()

    assert "## Summary" in workflow
    assert "## Validation" in workflow
    assert "## Evidence" in workflow
    assert '--body-file "$pr_body_file"' in workflow
    assert "Auto-generated release changelog" not in workflow


def test_existing_release_pr_body_is_fixed_before_ci_triggering_push() -> None:
    workflow = _workflow_text()

    assert workflow.index('gh pr edit "$pr_number" --body-file "$pr_body_file"') < workflow.index(
        'git push --force-with-lease origin "$branch"'
    )


def test_release_pr_waits_for_checks_before_admin_merge() -> None:
    workflow = _workflow_text()

    assert workflow.index(
        'pr_head="$(gh pr view "$pr_number" --json headRefOid --jq \'.headRefOid\')"'
    ) < workflow.index('gh pr checks "$pr_number" --watch --fail-fast --interval 10')
    assert workflow.index('gh pr checks "$pr_number" --watch --fail-fast --interval 10') < workflow.index(
        'gh pr merge "$pr_number" --admin --squash --delete-branch'
    )
