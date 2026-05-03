#!/usr/bin/env python3
"""Insert a release section in CHANGELOG.md when one is missing."""

from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
from pathlib import Path

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
UNRELEASED_RE = re.compile(r"^## \[Unreleased\]\s*$", re.MULTILINE)
RELEASE_HEADING_RE = re.compile(r"^## \[(?P<version>\d+\.\d+\.\d+)\](?:\s+-\s+\d{4}-\d{2}-\d{2})?\s*$", re.MULTILINE)
SKIP_SUBJECT_PREFIXES = (
    "chore: bump version",
    "chore: update changelog",
)


def _git(repo_root: Path, args: list[str]) -> str:
    return subprocess.check_output(["git", *args], cwd=repo_root, text=True, stderr=subprocess.DEVNULL).strip()


def changelog_has_version(text: str, version: str) -> bool:
    return any(match.group("version") == version for match in RELEASE_HEADING_RE.finditer(text))


def previous_release_tag(repo_root: Path) -> str | None:
    try:
        tags = _git(repo_root, ["tag", "--list", "v[0-9]*", "--sort=-v:refname"])
    except subprocess.CalledProcessError:
        return None
    return next((tag for tag in tags.splitlines() if tag), None)


def release_subjects(repo_root: Path, previous_tag: str | None) -> list[str]:
    rev_range = f"{previous_tag}..HEAD" if previous_tag else "HEAD"
    try:
        raw = _git(repo_root, ["log", "--reverse", "--pretty=format:%s", rev_range])
    except subprocess.CalledProcessError:
        return []

    subjects: list[str] = []
    for line in raw.splitlines():
        subject = line.strip()
        if not subject:
            continue
        if subject.startswith(SKIP_SUBJECT_PREFIXES):
            continue
        if "[skip release]" in subject:
            continue
        subjects.append(subject)
    return subjects


def render_release_section(version: str, release_date: dt.date, subjects: list[str]) -> str:
    notes = subjects or ["Maintenance release."]
    bullets = "\n".join(f"- {subject}" for subject in notes)
    return f"## [{version}] - {release_date.isoformat()}\n\n### Changed\n{bullets}\n\n"


def insert_release_section(changelog: str, section: str) -> str:
    match = UNRELEASED_RE.search(changelog)
    if not match:
        raise ValueError("CHANGELOG.md is missing '## [Unreleased]' section")
    insert_at = match.end()
    return changelog[:insert_at] + "\n\n" + section.rstrip() + changelog[insert_at:]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd(), help="Repository root path")
    parser.add_argument("--version", required=True, help="Release version without leading 'v', e.g. 0.1.40")
    parser.add_argument("--date", default=dt.date.today().isoformat(), help="Release date in YYYY-MM-DD format")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not VERSION_RE.match(args.version):
        raise SystemExit(f"Invalid version: {args.version}")
    release_date = dt.date.fromisoformat(args.date)
    repo_root = args.repo_root.resolve()
    changelog_path = repo_root / "CHANGELOG.md"
    changelog = changelog_path.read_text(encoding="utf-8")

    if changelog_has_version(changelog, args.version):
        print(f"CHANGELOG.md already has [{args.version}]")
        return 0

    previous_tag = previous_release_tag(repo_root)
    subjects = release_subjects(repo_root, previous_tag)
    section = render_release_section(args.version, release_date, subjects)
    changelog_path.write_text(insert_release_section(changelog, section), encoding="utf-8")
    print(f"Inserted CHANGELOG.md section for [{args.version}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
