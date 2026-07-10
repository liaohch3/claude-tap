"""Unit contracts for the dashboard's side-by-side line comparison."""

from __future__ import annotations

import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _hi_model_lab_sessions(sessions: list[dict]) -> list[dict]:
    experiment = [
        {"model": "claude-fable-5", "agent_key": "claude-code"},
        {"model": "gpt-5.6-sol", "agent_key": "codex"},
    ]
    candidates = [
        session
        for session in sessions
        if session.get("record_count", 0) > 0 and str(session.get("first_user", "")).strip().lower() == "hi"
    ]
    pair = []
    for target in experiment:
        matches = [
            session
            for session in candidates
            if str(session.get("model", "")).lower() == target["model"]
            and str(session.get("agent_key", "")).lower() == target["agent_key"]
        ]
        if matches:
            pair.append(max(matches, key=lambda session: session.get("started_at", "")))
    return pair


def _default_compare_lab_pair(sessions: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for session in sessions:
        groups.setdefault(session.get("agent_key", "unknown"), []).append(session)
    for group in groups.values():
        latest = group[0]
        partner = next((item for item in group[1:] if item.get("model") != latest.get("model")), None)
        if partner:
            return [latest, partner]
    latest = sessions[0]
    partner = next((item for item in sessions[1:] if item.get("model") != latest.get("model")), None)
    return [latest, partner] if partner else sessions[:2]


def _line_diff_rows(left_text: str, right_text: str) -> list[tuple[str, str, str]]:
    """Mirror dashboard.html lineDiffRows for fast alignment coverage."""
    left = left_text.split("\n")[:800] if left_text else []
    right = right_text.split("\n")[:800] if right_text else []
    matrix = [[0] * (len(right) + 1) for _ in range(len(left) + 1)]
    for left_index in range(1, len(left) + 1):
        for right_index in range(1, len(right) + 1):
            if left[left_index - 1] == right[right_index - 1]:
                matrix[left_index][right_index] = matrix[left_index - 1][right_index - 1] + 1
            else:
                matrix[left_index][right_index] = max(
                    matrix[left_index - 1][right_index],
                    matrix[left_index][right_index - 1],
                )

    rows: list[tuple[str, str, str]] = []
    left_index = len(left)
    right_index = len(right)
    while left_index > 0 or right_index > 0:
        if left_index > 0 and right_index > 0 and left[left_index - 1] == right[right_index - 1]:
            rows.append((left[left_index - 1], right[right_index - 1], "same"))
            left_index -= 1
            right_index -= 1
        elif right_index > 0 and (
            left_index == 0 or matrix[left_index][right_index - 1] > matrix[left_index - 1][right_index]
        ):
            rows.append(("", right[right_index - 1], "added"))
            right_index -= 1
        else:
            rows.append((left[left_index - 1], "", "removed"))
            left_index -= 1
    return _align_modified_rows(list(reversed(rows)))


def _align_modified_rows(rows: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    aligned: list[tuple[str, str, str]] = []
    index = 0
    while index < len(rows):
        if rows[index][2] == "same":
            aligned.append(rows[index])
            index += 1
            continue

        run: list[tuple[str, str, str]] = []
        while index < len(rows) and rows[index][2] != "same":
            run.append(rows[index])
            index += 1
        removed = [row for row in run if row[2] == "removed"]
        added = [row for row in run if row[2] == "added"]
        for offset in range(max(len(removed), len(added))):
            left = removed[offset][0] if offset < len(removed) else ""
            right = added[offset][1] if offset < len(added) else ""
            kind = "modified" if left and right else "removed" if left else "added"
            aligned.append((left, right, kind))
    return aligned


def test_line_diff_rows_keeps_shared_prompt_lines_aligned() -> None:
    rows = _line_diff_rows(
        "You are an agent.\nUse Read.\nAnswer briefly.",
        "You are an agent.\nUse Bash.\nAnswer briefly.",
    )

    assert rows[0] == ("You are an agent.", "You are an agent.", "same")
    assert ("Use Read.", "Use Bash.", "modified") in rows
    assert rows[-1] == ("Answer briefly.", "Answer briefly.", "same")


def test_line_diff_rows_handles_content_present_on_only_one_side() -> None:
    assert _line_diff_rows("", "tool: Research") == [
        ("", "tool: Research", "added"),
    ]


def test_hi_model_lab_pairs_latest_fable_with_codex_cli_sol_probe() -> None:
    sessions = [
        {
            "id": "fable-old",
            "model": "claude-fable-5",
            "agent_key": "claude-code",
            "first_user": "Hi",
            "record_count": 1,
            "started_at": "2026-07-10T10:00:00Z",
        },
        {
            "id": "fable-new",
            "model": "claude-fable-5",
            "agent_key": "claude-code",
            "first_user": "hi",
            "record_count": 1,
            "started_at": "2026-07-10T11:00:00Z",
        },
        {
            "id": "sol-app",
            "model": "gpt-5.6-sol",
            "agent_key": "codex-app",
            "first_user": "Hi",
            "record_count": 1,
            "started_at": "2026-07-10T13:00:00Z",
        },
        {
            "id": "sol-cli",
            "model": "gpt-5.6-sol",
            "agent_key": "codex",
            "first_user": "Hi",
            "record_count": 4,
            "started_at": "2026-07-10T12:00:00Z",
        },
    ]

    pair = _hi_model_lab_sessions(sessions)

    assert [session["id"] for session in pair] == ["fable-new", "sol-cli"]


def test_default_diff_lab_keeps_comparison_within_one_agent() -> None:
    sessions = [
        {"id": "sol", "model": "gpt-5.6-sol", "agent_key": "codex"},
        {"id": "opus", "model": "claude-opus-4-8", "agent_key": "claude-code"},
        {"id": "fable", "model": "claude-fable-5", "agent_key": "claude-code"},
    ]

    pair = _default_compare_lab_pair(sessions)

    assert [session["id"] for session in pair] == ["opus", "fable"]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for dashboard JS unit tests")
def test_comparison_tool_summary_skips_leading_blank_description_lines() -> None:
    dashboard_html = (REPO_ROOT / "claude_tap" / "dashboard.html").read_text(encoding="utf-8")
    match = re.search(r"function comparisonToolSummary\(tool\) \{.*?\n\}", dashboard_html, re.DOTALL)
    assert match, "comparisonToolSummary not found in dashboard.html"

    script = match.group(0) + textwrap.dedent(
        r"""

        const assert = require('assert/strict');
        assert.equal(
          comparisonToolSummary({ name: 'ListMcpResourcesTool', value: { description: '\nList available resources.\nDetails follow.' } }),
          'List available resources.',
        );
        assert.equal(
          comparisonToolSummary({ name: 'Read', value: { description: 'Reads a file.\nMore.' } }),
          'Reads a file.',
        );
        assert.equal(comparisonToolSummary({ name: 'Bash', value: { description: '\n  \n' } }), 'Bash');
        assert.equal(comparisonToolSummary({ name: 'Bash', value: {} }), 'Bash');
        assert.equal(comparisonToolSummary(null), '—');
        console.log('ok');
        """
    )
    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
