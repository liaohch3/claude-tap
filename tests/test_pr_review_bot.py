"""Tests for webhook review bot helpers."""

from __future__ import annotations

import hashlib
import hmac

from scripts.pr_review_bot import (
    build_review_prompt,
    should_process_pull_request,
    verify_webhook_signature,
)


def test_verify_webhook_signature_accepts_valid_hmac() -> None:
    secret = "top-secret"
    body = b'{"action":"opened"}'
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    assert verify_webhook_signature(secret, body, f"sha256={digest}") is True
    assert verify_webhook_signature(secret, body, "sha256=invalid") is False


def test_event_filter_only_accepts_opened_and_synchronize() -> None:
    payload = {
        "action": "opened",
        "sender": {"login": "octocat"},
        "pull_request": {"number": 42},
    }
    ignore_users = {"github-actions[bot]"}

    ok_opened, _ = should_process_pull_request("pull_request", payload, ignore_users)
    assert ok_opened is True

    payload["action"] = "synchronize"
    ok_sync, _ = should_process_pull_request("pull_request", payload, ignore_users)
    assert ok_sync is True

    payload["action"] = "edited"
    ok_edited, _ = should_process_pull_request("pull_request", payload, ignore_users)
    assert ok_edited is False


def test_event_filter_rejects_bot_sender() -> None:
    payload = {
        "action": "opened",
        "sender": {"login": "dependabot[bot]"},
        "pull_request": {"number": 42},
    }
    ok, reason = should_process_pull_request("pull_request", payload, {"dependabot[bot]"})
    assert ok is False
    assert "ignored sender" in reason


def test_build_review_prompt_includes_pr_metadata_and_diff() -> None:
    template = "PR #{pr_number}: {pr_title}\n{pr_body}\n{head_ref}->{base_ref}\n{diff_text}"
    prompt = build_review_prompt(
        template_text=template,
        pr_number=7,
        pr_title="feat: add worker",
        pr_body="Describe changes",
        head_ref="feature/worker",
        base_ref="main",
        diff_text="diff --git a/x b/x",
    )

    assert "PR #7" in prompt
    assert "feat: add worker" in prompt
    assert "feature/worker->main" in prompt
    assert "diff --git" in prompt
