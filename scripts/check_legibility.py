#!/usr/bin/env python3
"""Deterministic legibility checks for maintainer standards, plans, and architecture manifest."""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
REQUIRED_STANDARDS_KEYS = ("owner", "last_reviewed", "source_of_truth")
ALLOWED_PLAN_STATUS = {"active", "completed", "cancelled"}
AGENT_DOCS_DIR = Path(".agents") / "docs"
STANDARDS_DIR = AGENT_DOCS_DIR / "standards"
PLANS_DIR = AGENT_DOCS_DIR / "plans"
ARCHITECTURE_MANIFEST = AGENT_DOCS_DIR / "architecture" / "manifest.yaml"


@dataclass
class CheckResult:
    failures: list[str]
    warnings: list[str]


def parse_frontmatter(markdown_text: str) -> dict[str, str]:
    lines = markdown_text.splitlines()
    if len(lines) < 3 or lines[0].strip() != "---":
        return {}
    frontmatter_lines: list[str] = []
    for line in lines[1:]:
        if line.strip() == "---":
            break
        frontmatter_lines.append(line)
    else:
        return {}

    fields: dict[str, str] = {}
    for line in frontmatter_lines:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip()
    return fields


def parse_expected_paths(manifest_text: str) -> list[str]:
    lines = manifest_text.splitlines()
    in_expected_paths = False
    expected_paths: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "expected_paths:":
            in_expected_paths = True
            continue
        if in_expected_paths:
            if not stripped.startswith("- "):
                break
            value = stripped[2:].strip().strip("'\"")
            if value:
                expected_paths.append(value)

    return expected_paths


def parse_iso_date(date_str: str) -> dt.date | None:
    if not ISO_DATE_RE.match(date_str):
        return None
    try:
        return dt.date.fromisoformat(date_str)
    except ValueError:
        return None


def _is_within_repo_root(repo_root: Path, candidate_path: Path) -> bool:
    try:
        candidate_path.relative_to(repo_root)
        return True
    except ValueError:
        return False


def _strip_fenced_code_blocks(markdown_text: str) -> str:
    kept_lines: list[str] = []
    in_fence = False
    fence_char = ""
    fence_len = 0

    for line in markdown_text.splitlines():
        stripped = line.lstrip()
        if not in_fence and (stripped.startswith("```") or stripped.startswith("~~~")):
            in_fence = True
            fence_char = stripped[0]
            fence_len = len(stripped) - len(stripped.lstrip(fence_char))
            continue

        if in_fence:
            if stripped.startswith(fence_char * fence_len):
                in_fence = False
            continue

        kept_lines.append(line)

    return "\n".join(kept_lines)


def check_standards_freshness(
    repo_root: Path, *, freshness_days: int, strict_freshness: bool, today: dt.date
) -> CheckResult:
    failures: list[str] = []
    warnings: list[str] = []
    standards_dir = repo_root / STANDARDS_DIR

    for file_path in sorted(standards_dir.glob("*.md")):
        frontmatter = parse_frontmatter(file_path.read_text(encoding="utf-8"))

        for key in REQUIRED_STANDARDS_KEYS:
            if not frontmatter.get(key):
                failures.append(f"{file_path}: missing frontmatter key '{key}'")

        last_reviewed_raw = frontmatter.get("last_reviewed")
        if not last_reviewed_raw:
            continue

        reviewed_date = parse_iso_date(last_reviewed_raw)
        if reviewed_date is None:
            failures.append(f"{file_path}: last_reviewed must be ISO date YYYY-MM-DD")
            continue

        age_days = (today - reviewed_date).days
        if age_days > freshness_days:
            msg = (
                f"{file_path}: last_reviewed {last_reviewed_raw} is stale "
                f"({age_days} days old, threshold={freshness_days})"
            )
            if strict_freshness:
                failures.append(msg)
            else:
                warnings.append(msg)

    return CheckResult(failures=failures, warnings=warnings)


def check_architecture_manifest(repo_root: Path) -> CheckResult:
    failures: list[str] = []
    resolved_repo_root = repo_root.resolve()
    manifest_path = repo_root / ARCHITECTURE_MANIFEST
    if not manifest_path.exists():
        return CheckResult(failures=[f"{manifest_path}: missing manifest file"], warnings=[])

    expected_paths = parse_expected_paths(manifest_path.read_text(encoding="utf-8"))
    if not expected_paths:
        failures.append(f"{manifest_path}: expected_paths list is empty or missing")
        return CheckResult(failures=failures, warnings=[])

    for relative_path in expected_paths:
        candidate_path = Path(relative_path)
        if candidate_path.is_absolute():
            failures.append(f"{manifest_path}: expected path must be relative: {relative_path}")
            continue

        absolute_path = (repo_root / candidate_path).resolve()
        if not _is_within_repo_root(resolved_repo_root, absolute_path):
            failures.append(f"{manifest_path}: expected path resolves outside repo root: {relative_path}")
            continue

        if not absolute_path.exists():
            failures.append(f"{manifest_path}: expected path missing: {relative_path}")

    return CheckResult(failures=failures, warnings=[])


def check_plan_state_drift(repo_root: Path) -> CheckResult:
    failures: list[str] = []
    plans_dir = repo_root / PLANS_DIR

    for file_path in sorted(plans_dir.glob("**/*.md")):
        text = file_path.read_text(encoding="utf-8")
        frontmatter = parse_frontmatter(text)
        status = frontmatter.get("status")

        if status not in ALLOWED_PLAN_STATUS:
            failures.append(f"{file_path}: status must be one of {sorted(ALLOWED_PLAN_STATUS)}")
            continue

        if status == "completed" and "- [ ]" in _strip_fenced_code_blocks(text):
            failures.append(f"{file_path}: completed plan still contains unchecked TODO checkboxes")

    return CheckResult(failures=failures, warnings=[])


def run_checks(repo_root: Path, *, freshness_days: int, strict_freshness: bool, today: dt.date) -> CheckResult:
    failures: list[str] = []
    warnings: list[str] = []

    for result in (
        check_standards_freshness(
            repo_root,
            freshness_days=freshness_days,
            strict_freshness=strict_freshness,
            today=today,
        ),
        check_architecture_manifest(repo_root),
        check_plan_state_drift(repo_root),
    ):
        failures.extend(result.failures)
        warnings.extend(result.warnings)

    return CheckResult(failures=failures, warnings=warnings)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root", type=Path, default=Path.cwd(), help="Repository root path (default: current dir)"
    )
    parser.add_argument(
        "--freshness-days",
        type=int,
        default=60,
        help="Warn when standards last_reviewed is older than this many days (default: 60)",
    )
    parser.add_argument(
        "--strict-freshness",
        action="store_true",
        help="Promote stale standards freshness warnings to failures",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_checks(
        args.repo_root,
        freshness_days=args.freshness_days,
        strict_freshness=args.strict_freshness,
        today=dt.date.today(),
    )

    for warning in result.warnings:
        print(f"WARNING: {warning}")
    for failure in result.failures:
        print(f"ERROR: {failure}")

    if result.warnings:
        print(f"Legibility check warnings: {len(result.warnings)}")
    if result.failures:
        print(f"Legibility check failures: {len(result.failures)}")
        return 1

    print("Legibility checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
