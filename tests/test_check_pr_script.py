"""Focused tests for scripts/check_pr.sh verdict behavior."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "check_pr.sh"


GH_STUB = """#!/bin/sh
set -eu

cmd1=${1:-}
cmd2=${2:-}

if [ "$cmd1" = "repo" ] && [ "$cmd2" = "view" ]; then
  echo "octo/demo"
  exit 0
fi

if [ "$cmd1" = "pr" ] && [ "$cmd2" = "view" ]; then
  # Check if this is a body-only query
  case "$*" in
    *--json\\ body*)
      echo '## Evidence\n![trace](https://example.com/evidence/trace.png)'
      exit 0
      ;;
  esac
  cat <<'EOF'
Improve merge readiness automation
OPEN
false
CLEAN
feature/checker
main
https://github.com/octo/demo/pull/42
EOF
  exit 0
fi

if [ "$cmd1" = "pr" ] && [ "$cmd2" = "checks" ]; then
  if [ "${GH_CHECKS_MODE:-}" = "pending_exit8" ]; then
    cat <<'EOF'
pass	lint	success
pending	test	pending
EOF
    exit 8
  fi
  cat <<'EOF'
pass	lint	success
pass	test	success
EOF
  exit 0
fi

echo "unexpected gh args: $*" >&2
exit 1
"""

UV_STUB = """#!/bin/sh
set -eu

if [ "${FAIL_GATE:-}" = "pytest" ] && [ "${1:-}" = "run" ] && [ "${2:-}" = "pytest" ]; then
  exit 1
fi
if [ "${FAIL_GATE:-}" = "ruff-check" ] && [ "${1:-}" = "run" ] && [ "${2:-}" = "ruff" ] && [ "${3:-}" = "check" ]; then
  exit 1
fi
if [ "${FAIL_GATE:-}" = "ruff-format" ] && [ "${1:-}" = "run" ] && [ "${2:-}" = "ruff" ] && [ "${3:-}" = "format" ]; then
  exit 1
fi
exit 0
"""


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def test_check_pr_reports_ready_with_no_tests(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(fake_bin / "gh", GH_STUB)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [str(SCRIPT_PATH), "42", "--no-tests"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=SCRIPT_PATH.parent.parent,
    )

    assert result.returncode == 0
    assert "Checks: pass=2 fail=0 pending=0 total=2" in result.stdout
    assert "VERDICT: READY" in result.stdout


def test_check_pr_reports_not_ready_when_local_gate_fails(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(fake_bin / "gh", GH_STUB)
    _write_executable(fake_bin / "uv", UV_STUB)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["FAIL_GATE"] = "pytest"

    result = subprocess.run(
        [str(SCRIPT_PATH), "42"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=SCRIPT_PATH.parent.parent,
    )

    assert result.returncode == 2
    assert "FAIL uv run pytest tests/ -x --timeout=60" in result.stdout
    assert "VERDICT: NOT_READY - local gates failed" in result.stdout


def test_check_pr_handles_pending_checks_exit_code(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(fake_bin / "gh", GH_STUB)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["GH_CHECKS_MODE"] = "pending_exit8"

    result = subprocess.run(
        [str(SCRIPT_PATH), "42", "--no-tests"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=SCRIPT_PATH.parent.parent,
    )

    assert result.returncode == 2
    assert "Checks: pass=1 fail=0 pending=1 total=2" in result.stdout
    assert "VERDICT: NOT_READY - 1 CI check(s) pending" in result.stdout
