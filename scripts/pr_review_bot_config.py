#!/usr/bin/env python3
"""Configuration helpers for the local PR review bot."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ReviewBotConfig:
    webhook_secret: str
    allow_insecure_webhooks: bool
    review_agent: str
    output_language: str
    repo_path: Path
    review_timeout: int
    port: int
    log_file: Path
    ignore_users: frozenset[str]


def load_config() -> ReviewBotConfig:
    webhook_secret = os.getenv("PR_REVIEW_WEBHOOK_SECRET", "")
    review_agent = (os.getenv("PR_REVIEW_AGENT", "codex") or "").strip().lower()
    if review_agent not in {"codex", "claude"}:
        review_agent = "codex"
    output_language = (os.getenv("PR_REVIEW_OUTPUT_LANGUAGE", "zh") or "").strip().lower()
    if output_language not in {"zh", "en"}:
        output_language = "zh"
    insecure_raw = (os.getenv("PR_REVIEW_ALLOW_INSECURE_WEBHOOKS", "") or "").strip().lower()
    allow_insecure_webhooks = insecure_raw in {"1", "true", "yes", "on"}
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
        allow_insecure_webhooks=allow_insecure_webhooks,
        review_agent=review_agent,
        output_language=output_language,
        repo_path=repo_path,
        review_timeout=review_timeout,
        port=port,
        log_file=log_file,
        ignore_users=ignore_users,
    )
