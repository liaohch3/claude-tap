#!/usr/bin/env python3
"""Ensure released tags are documented in CHANGELOG.md."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

TAG_RE = re.compile(r"^v(?P<version>\d+\.\d+\.\d+)$")
CHANGELOG_HEADING_RE = re.compile(r"^## \[(?P<version>\d+\.\d+\.\d+)\](?:\s+-\s+\d{4}-\d{2}-\d{2})?\s*$", re.MULTILINE)


def normalize_tag(tag: str) -> str | None:
    match = TAG_RE.match(tag.strip())
    return match.group("version") if match else None


def changelog_versions(text: str) -> set[str]:
    return {match.group("version") for match in CHANGELOG_HEADING_RE.finditer(text)}


def _git(repo_root: Path, args: list[str]) -> str:
    return subprocess.check_output(["git", *args], cwd=repo_root, text=True, stderr=subprocess.DEVNULL).strip()


def current_exact_tag(repo_root: Path) -> str | None:
    try:
        tag = _git(repo_root, ["describe", "--exact-match", "--tags", "HEAD"])
    except subprocess.CalledProcessError:
        return None
    return tag if normalize_tag(tag) else None


def latest_version_tag(repo_root: Path) -> str | None:
    try:
        tags = _git(repo_root, ["tag", "--list", "v[0-9]*", "--sort=-v:refname"])
    except subprocess.CalledProcessError:
        return None
    for tag in tags.splitlines():
        if normalize_tag(tag):
            return tag
    return None


def required_tag(repo_root: Path, explicit_tag: str | None) -> str | None:
    if explicit_tag:
        return explicit_tag
    return current_exact_tag(repo_root) or latest_version_tag(repo_root)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd(), help="Repository root path")
    parser.add_argument("--tag", help="Release tag to require, e.g. v0.1.40")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = args.repo_root.resolve()
    tag = required_tag(repo_root, args.tag)
    if tag is None:
        print("No release tag found; skipping changelog release coverage check.")
        return 0

    version = normalize_tag(tag)
    if version is None:
        print(f"ERROR: unsupported release tag format: {tag}", file=sys.stderr)
        return 1

    changelog_path = repo_root / "CHANGELOG.md"
    try:
        documented_versions = changelog_versions(changelog_path.read_text(encoding="utf-8"))
    except OSError as exc:
        print(f"ERROR: cannot read {changelog_path}: {exc}", file=sys.stderr)
        return 1

    if version not in documented_versions:
        print(f"ERROR: CHANGELOG.md is missing release section for [{version}] ({tag})", file=sys.stderr)
        return 1

    print(f"Changelog release coverage passed for {tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
