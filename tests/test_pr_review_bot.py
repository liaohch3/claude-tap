"""Tests for webhook review bot helpers."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import time
from collections.abc import Iterable
from pathlib import Path

from scripts.pr_review_bot import (
    ReviewBotConfig,
    ReviewOrchestrator,
    build_review_prompt,
    create_app,
    extract_decision,
    fetch_diff,
    load_config,
    post_review,
    should_process_pull_request,
    verify_webhook_signature,
)


def test_verify_webhook_signature_accepts_valid_hmac() -> None:
    secret = "top-secret"
    body = b'{"action":"opened"}'
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    assert verify_webhook_signature(secret, body, f"sha256={digest}") is True
    assert verify_webhook_signature(secret, body, "sha256=invalid") is False


def test_verify_webhook_signature_rejects_when_secret_missing() -> None:
    assert verify_webhook_signature("", b"{}", "sha256=abc") is False


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
    assert "ignore" in reason.lower()


def test_build_review_prompt_includes_pr_metadata_and_diff() -> None:
    pr = {
        "number": 7,
        "title": "feat: add worker",
        "body": "Describe changes",
        "head": {"ref": "feature/worker"},
        "base": {"ref": "main"},
    }
    prompt = build_review_prompt(pr, "diff --git a/x b/x", "zh")

    assert "7" in prompt
    assert "feat: add worker" in prompt
    assert "diff --git" in prompt


def test_load_config_normalizes_agent_and_language(monkeypatch) -> None:
    monkeypatch.setenv("PR_REVIEW_AGENT", " Claude ")
    monkeypatch.setenv("PR_REVIEW_OUTPUT_LANGUAGE", " EN ")
    config = load_config()

    assert config.review_agent == "claude"
    assert config.output_language == "en"


def test_load_config_fallbacks_for_invalid_agent_and_language(monkeypatch) -> None:
    monkeypatch.setenv("PR_REVIEW_AGENT", "bad")
    monkeypatch.setenv("PR_REVIEW_OUTPUT_LANGUAGE", "bad")
    config = load_config()

    assert config.review_agent == "codex"
    assert config.output_language == "zh"


def test_extract_decision_uses_strict_markers() -> None:
    text = """The options are APPROVE / REQUEST_CHANGES / COMMENT.
Decision: COMMENT
"""
    assert extract_decision(text) == "COMMENT"

    assert extract_decision("gh pr review 7 --request-changes --body x") == "REQUEST_CHANGES"
    assert extract_decision("gh pr review 7 --approve --body x") == "APPROVE"


def _base_config() -> ReviewBotConfig:
    return ReviewBotConfig(
        webhook_secret="top-secret",
        allow_insecure_webhooks=False,
        repo_path=Path(os.getcwd()),
        review_agent="codex",
        output_language="zh",
        review_timeout=30,
        port=3456,
        ignore_users=frozenset(),
        log_file=Path("/tmp/pr-review-bot-test.log"),
    )


class _NoopOrchestrator:
    def submit_review(self, payload: dict[str, object]) -> None:
        """No-op."""


async def _post_webhook(
    app,
    *,
    body: bytes,
    headers: Iterable[tuple[str, str]],
) -> tuple[int, dict[str, object]]:
    header_bytes = [(k.lower().encode("utf-8"), v.encode("utf-8")) for k, v in headers]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/webhook",
        "raw_path": b"/webhook",
        "query_string": b"",
        "headers": header_bytes,
        "client": ("testclient", 123),
        "server": ("testserver", 80),
    }
    sent_start: dict[str, object] = {}
    sent_body = b""
    done = False

    async def receive() -> dict[str, object]:
        nonlocal done
        if not done:
            done = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        nonlocal sent_start, sent_body
        if message["type"] == "http.response.start":
            sent_start = message
        elif message["type"] == "http.response.body":
            sent_body += message.get("body", b"")

    await app(scope, receive, send)
    return int(sent_start["status"]), json.loads(sent_body.decode("utf-8"))


async def test_webhook_rejects_invalid_json_with_400() -> None:
    app = create_app(_base_config(), _NoopOrchestrator())
    status, payload = await _post_webhook(
        app,
        body=b"{bad",
        headers=[("x-github-event", "pull_request")],
    )
    assert status == 400
    assert payload["error"] == "invalid json payload"


async def test_webhook_requires_signature_when_secret_is_set() -> None:
    app = create_app(_base_config(), _NoopOrchestrator())
    payload = {
        "action": "opened",
        "sender": {"login": "octocat", "type": "User"},
        "pull_request": {"number": 1},
    }
    status, response = await _post_webhook(
        app,
        body=json.dumps(payload).encode("utf-8"),
        headers=[("x-github-event", "pull_request"), ("content-type", "application/json")],
    )
    assert status == 401
    assert response["error"] == "missing signature"


async def test_webhook_requires_wrapped_signature_when_secret_is_set() -> None:
    app = create_app(_base_config(), _NoopOrchestrator())
    wrapped = {
        "payload": json.dumps(
            {
                "action": "opened",
                "sender": {"login": "octocat", "type": "User"},
                "pull_request": {"number": 1},
            }
        ),
        "x-github-event": "pull_request",
    }
    status, response = await _post_webhook(
        app,
        body=json.dumps(wrapped).encode("utf-8"),
        headers=[("x-github-event", "pull_request"), ("content-type", "application/json")],
    )
    assert status == 401
    assert response["error"] == "missing signature"


def test_fetch_diff_uses_force_refspec_and_base_fetch(monkeypatch) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((cmd, kwargs))
        if cmd[:2] == ["git", "diff"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="diff-content", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("scripts.pr_review_bot.subprocess.run", _fake_run)

    diff = fetch_diff(Path("/tmp/repo"), 28, "main")

    assert diff == "diff-content"
    assert calls[0][0] == ["git", "fetch", "origin", "+pull/28/head:pr-28"]
    assert calls[1][0] == ["git", "fetch", "origin", "main"]
    assert calls[2][0] == ["git", "diff", "origin/main...pr-28"]
    assert all(call_kwargs["check"] is True for _, call_kwargs in calls)


def test_fetch_diff_raises_when_git_fetch_fails(monkeypatch) -> None:
    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(returncode=1, cmd=cmd, output="", stderr="fetch failed")

    monkeypatch.setattr("scripts.pr_review_bot.subprocess.run", _fake_run)

    try:
        fetch_diff(Path("/tmp/repo"), 28, "main")
    except subprocess.CalledProcessError as exc:
        assert "fetch failed" in str(exc.stderr)
    else:
        raise AssertionError("fetch_diff should raise CalledProcessError")


def test_post_review_uses_review_for_approve(monkeypatch) -> None:
    commands: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("scripts.pr_review_bot.subprocess.run", _fake_run)
    post_review(28, "LGTM", "owner/repo", "APPROVE")
    assert commands[0][:4] == ["gh", "pr", "review", "28"]
    assert "--approve" in commands[0]


def test_post_review_uses_review_for_request_changes(monkeypatch) -> None:
    commands: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("scripts.pr_review_bot.subprocess.run", _fake_run)
    post_review(28, "Needs fixes", "owner/repo", "REQUEST_CHANGES")
    assert commands[0][:4] == ["gh", "pr", "review", "28"]
    assert "--request-changes" in commands[0]


def test_post_review_uses_comment_for_default(monkeypatch) -> None:
    commands: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("scripts.pr_review_bot.subprocess.run", _fake_run)
    post_review(28, "General comment", "owner/repo", "COMMENT")
    assert commands[0][:4] == ["gh", "pr", "comment", "28"]


def test_submit_review_cancels_previous_worker(monkeypatch) -> None:
    config = _base_config()
    orchestrator = ReviewOrchestrator(config)
    posted: list[tuple[int, str]] = []
    run_calls = {"count": 0}

    payload = {
        "repository": {"full_name": "owner/repo"},
        "pull_request": {
            "number": 7,
            "base": {"ref": "main"},
            "head": {"ref": "feature"},
            "title": "Test PR",
            "body": "",
        },
    }

    def _fake_fetch_diff(repo_path: Path, pr_number: int, base_ref: str) -> str:
        assert repo_path == config.repo_path
        assert pr_number == 7
        assert base_ref == "main"
        return "diff --git a/x b/x"

    def _fake_run_review(
        _config: ReviewBotConfig,
        _pr: dict[str, object],
        _diff: str,
        cancel_event,
    ) -> str:
        run_calls["count"] += 1
        if run_calls["count"] == 1:
            while not cancel_event.is_set():
                time.sleep(0.01)
            raise TimeoutError("review cancelled by newer event")
        return "Decision: COMMENT\nAll good."

    def _fake_post_review(pr_number: int, review_text: str, repo: str, recommendation: str) -> None:
        posted.append((pr_number, recommendation))
        assert review_text == "All good."
        assert repo == "owner/repo"

    monkeypatch.setattr("scripts.pr_review_bot.fetch_diff", _fake_fetch_diff)
    monkeypatch.setattr("scripts.pr_review_bot.run_review", _fake_run_review)
    monkeypatch.setattr("scripts.pr_review_bot.post_review", _fake_post_review)
    monkeypatch.setattr("scripts.pr_review_bot.notify_openclaw", lambda *args, **kwargs: None)

    orchestrator.submit_review(payload)
    time.sleep(0.05)
    orchestrator.submit_review(payload)

    deadline = time.time() + 2
    while time.time() < deadline:
        with orchestrator._lock:
            active = orchestrator._active.get(7)
        if active is None:
            break
        time.sleep(0.05)

    assert run_calls["count"] == 2
    assert posted == [(7, "COMMENT")]
