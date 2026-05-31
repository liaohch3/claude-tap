#!/usr/bin/env python3
"""Validate pull request body and changed-file policy gates."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

IMAGE_EXT_RE = re.compile(r"\.(?:png|jpe?g|gif|svg|webp)(?:[?#][^\s)]*)?$", re.IGNORECASE)
MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", re.IGNORECASE)
HTML_IMAGE_RE = re.compile(r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"']", re.IGNORECASE)
PLAIN_IMAGE_URL_RE = re.compile(
    r"https?://[^\s<>()\"']+\.(?:png|jpe?g|gif|svg|webp)(?:[?#][^\s<>()\"']*)?", re.IGNORECASE
)

SUMMARY_HEADINGS = {
    "summary",
    "problem",
    "goal",
    "refactor summary",
    "fix summary",
}
VALIDATION_HEADINGS = {
    "validation",
    "test plan",
    "results",
}

EVIDENCE_REQUIRED_PATHS = (
    "claude_tap/",
    "docs/support-matrix.md",
)
EVIDENCE_SCREENSHOT_ROOT = ".agents/evidence/pr/"
EVIDENCE_SCREENSHOT_KEYWORDS = (
    "browser",
    "dashboard",
    "e2e",
    "session",
    "trace",
    "viewer",
)
REAL_EVIDENCE_MARKERS = (
    ".traces/",
    "--run-real-e2e",
    "claude-tap --tap-client",
    "dashboard",
    "jsonl",
    "real e2e",
    "real trace",
    "run_real_e2e",
    "trace viewer",
    "uv run claude-tap",
    "viewer html",
)

SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bBearer\s+eyJ[A-Za-z0-9_.-]+"),
    re.compile(r"\b(?:ANTHROPIC|OPENAI|OPENROUTER|GITHUB)_[A-Z0-9_]*KEY\s*="),
)


@dataclass(frozen=True)
class PolicyResult:
    ok: bool
    failures: tuple[str, ...]
    warnings: tuple[str, ...]


def _heading_names(body: str) -> set[str]:
    headings: set[str] = set()
    for line in body.splitlines():
        match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", line)
        if match:
            headings.add(match.group(1).strip().lower())
    return headings


def _image_urls(body: str) -> list[str]:
    urls = [match.group(1).strip() for match in MARKDOWN_IMAGE_RE.finditer(body)]
    urls.extend(match.group(1).strip() for match in HTML_IMAGE_RE.finditer(body))
    urls.extend(match.group(0).strip() for match in PLAIN_IMAGE_URL_RE.finditer(body))

    unique: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if url not in seen:
            seen.add(url)
            unique.append(url)
    return unique


def _raw_image_urls(urls: list[str]) -> list[str]:
    return [url for url in urls if "://raw.githubusercontent.com/" in url and IMAGE_EXT_RE.search(url)]


def _raw_image_paths(urls: list[str]) -> list[str]:
    paths: list[str] = []
    for url in urls:
        parsed = urlparse(url)
        if parsed.netloc != "raw.githubusercontent.com":
            continue
        parts = [unquote(part) for part in parsed.path.split("/") if part]
        if ".agents" in parts:
            paths.append("/".join(parts[parts.index(".agents") :]))
        elif len(parts) >= 4:
            paths.append("/".join(parts[3:]))
    return paths


def _body_without_image_references(body: str) -> str:
    stripped = MARKDOWN_IMAGE_RE.sub("", body)
    stripped = HTML_IMAGE_RE.sub("", stripped)
    return PLAIN_IMAGE_URL_RE.sub("", stripped)


def _has_real_evidence_source(body: str) -> bool:
    lower = _body_without_image_references(body).lower()
    return any(marker in lower for marker in REAL_EVIDENCE_MARKERS)


def _evidence_required_paths(changed_files: list[str]) -> list[str]:
    required: list[str] = []
    for path in changed_files:
        normalized = path.strip()
        if not normalized:
            continue
        if normalized.startswith(EVIDENCE_REQUIRED_PATHS):
            required.append(normalized)
    return required


def _dangerous_files(changed_files: list[str]) -> list[str]:
    dangerous: list[str] = []
    for path in changed_files:
        normalized = path.strip()
        if not normalized:
            continue
        name = Path(normalized).name.lower()
        suffix = Path(normalized).suffix.lower()
        if normalized.startswith(".traces/") and suffix in {".jsonl", ".html", ".log"}:
            dangerous.append(normalized)
        elif name.startswith("trace_") and suffix in {".jsonl", ".html", ".log"}:
            dangerous.append(normalized)
        elif name in {".env", "id_rsa", "id_ed25519"}:
            dangerous.append(normalized)
    return dangerous


def validate_policy(body: str, changed_files: list[str]) -> PolicyResult:
    failures: list[str] = []
    warnings: list[str] = []
    body = body.strip()

    if not body:
        failures.append("PR body is empty")
        return PolicyResult(ok=False, failures=tuple(failures), warnings=())

    headings = _heading_names(body)
    if not headings.intersection(SUMMARY_HEADINGS):
        failures.append("PR body is missing a Summary, Problem, Goal, or equivalent section")
    if not headings.intersection(VALIDATION_HEADINGS):
        failures.append("PR body is missing a Validation, Test plan, or Results section")

    for pattern in SECRET_PATTERNS:
        if pattern.search(body):
            failures.append("PR body appears to contain a secret or bearer token")
            break

    image_urls = _image_urls(body)
    raw_urls = _raw_image_urls(image_urls)
    non_raw_urls = [url for url in image_urls if url not in raw_urls]
    if non_raw_urls:
        failures.append("PR image evidence must use raw.githubusercontent.com URLs")

    evidence_paths = _evidence_required_paths(changed_files)
    if evidence_paths and not raw_urls:
        failures.append(
            "PR changes runtime/viewer/client behavior but has no raw.githubusercontent.com screenshot evidence"
        )
        warnings.append("Evidence-triggering files: " + ", ".join(evidence_paths[:8]))
    elif evidence_paths:
        raw_paths = _raw_image_paths(raw_urls)
        evidence_image_paths = [path for path in raw_paths if path.startswith(EVIDENCE_SCREENSHOT_ROOT)]
        if not evidence_image_paths:
            failures.append("PR runtime screenshot evidence must link to committed .agents/evidence/pr/ images")
        elif not any(
            any(keyword in Path(path).name.lower() for keyword in EVIDENCE_SCREENSHOT_KEYWORDS)
            for path in evidence_image_paths
        ):
            failures.append(
                "PR runtime screenshot evidence must be trace/viewer/dashboard evidence, not test-output art"
            )
        if not _has_real_evidence_source(body):
            failures.append("PR runtime evidence must document the real trace/viewer source used for screenshots")

    dangerous = _dangerous_files(changed_files)
    if dangerous:
        failures.append("PR includes raw trace, generated viewer, log, or secret-like files")
        warnings.append("Blocked files: " + ", ".join(dangerous[:8]))

    return PolicyResult(ok=not failures, failures=tuple(failures), warnings=tuple(warnings))


def _load_body(args: argparse.Namespace) -> str:
    if args.body is not None:
        return args.body
    if args.body_file:
        return Path(args.body_file).read_text(encoding="utf-8")
    event_path = args.event_path or os.environ.get("GITHUB_EVENT_PATH")
    if event_path:
        data = json.loads(Path(event_path).read_text(encoding="utf-8"))
        body = data.get("pull_request", {}).get("body")
        if isinstance(body, str):
            return body
    raise SystemExit("error: provide --body, --body-file, or --event-path with pull_request.body")


def _load_changed_files(args: argparse.Namespace) -> list[str]:
    if args.changed_file:
        return args.changed_file
    if args.changed_files_file:
        return [
            line.strip()
            for line in Path(args.changed_files_file).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--body", help="PR body text")
    parser.add_argument("--body-file", help="File containing the PR body")
    parser.add_argument("--event-path", help="GitHub event JSON path")
    parser.add_argument("--changed-files-file", help="File containing changed paths, one per line")
    parser.add_argument("--changed-file", action="append", default=[], help="Changed path; repeatable")
    args = parser.parse_args(argv)

    result = validate_policy(_load_body(args), _load_changed_files(args))
    if result.ok:
        print("PR Policy: PASS")
        for warning in result.warnings:
            print(f"  WARN {warning}")
        return 0

    print("PR Policy: FAIL")
    for failure in result.failures:
        print(f"  FAIL {failure}")
    for warning in result.warnings:
        print(f"  INFO {warning}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
