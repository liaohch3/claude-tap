#!/usr/bin/env python3
"""Configuration helpers for the local PR review bot."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ReviewBotConfig:
    webhook_secret: str
    review_agent: str
    repo_path: Path
    review_timeout: int
    port: int
    log_file: Path
    ignore_users: frozenset[str]


def load_config() -> ReviewBotConfig:
    webhook_secret = os.getenv("PR_REVIEW_WEBHOOK_SECRET", "")
    review_agent = os.getenv("PR_REVIEW_AGENT", "codex").strip().lower() or "codex"
    repo_path = Path(os.getenv("PR_REVIEW_REPO_PATH", Path.cwd())).expanduser().resolve()
    review_timeout = int(os.getenv("PR_REVIEW_TIMEOUT", "600"))
    port = int(os.getenv("PR_REVIEW_PORT", "3456"))
    log_file = Path(os.getenv("PR_REVIEW_LOG_FILE", "/tmp/pr-review-bot.log"))
    ignore_users = frozenset(
        value.strip()
        for value in os.getenv(
            "PR_REVIEW_IGNORE_USERS",
            "github-actions[bot],dependabot[bot],claude[bot],codex[bot]",
        ).split(",")
        if value.strip()
    )
    return ReviewBotConfig(
        webhook_secret=webhook_secret,
        review_agent=review_agent,
        repo_path=repo_path,
        review_timeout=review_timeout,
        port=port,
        log_file=log_file,
        ignore_users=ignore_users,
    )
