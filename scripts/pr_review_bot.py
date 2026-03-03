#!/usr/bin/env python3
"""Webhook-driven local PR review bot."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import shlex
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOG = logging.getLogger("pr-review-bot")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class ReviewBotConfig:
    webhook_secret: str
    repo_path: str
    review_agent: str
    review_timeout: int
    port: int
    ignore_users: set[str]
    log_file: str


def load_config() -> ReviewBotConfig:
    import os

    return ReviewBotConfig(
        webhook_secret=os.environ.get("PR_REVIEW_WEBHOOK_SECRET", ""),
        repo_path=os.environ.get("PR_REVIEW_REPO_PATH", os.getcwd()),
        review_agent=os.environ.get("PR_REVIEW_AGENT", "codex"),
        review_timeout=int(os.environ.get("PR_REVIEW_TIMEOUT", "600")),
        port=int(os.environ.get("PR_REVIEW_PORT", "3456")),
        ignore_users=set(
            u.strip()
            for u in os.environ.get(
                "PR_REVIEW_IGNORE_USERS",
                "github-actions[bot],dependabot[bot]",
            ).split(",")
            if u.strip()
        ),
        log_file=os.environ.get("PR_REVIEW_LOG_FILE", "/tmp/pr-review-bot.log"),
    )


def setup_logging(log_file: str) -> None:
    fmt = "%(asctime)s %(levelname)s %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt)
    if log_file:
        fh = logging.FileHandler(log_file)
        fh.setFormatter(logging.Formatter(fmt))
        logging.getLogger().addHandler(fh)


# ---------------------------------------------------------------------------
# Webhook helpers
# ---------------------------------------------------------------------------


def verify_webhook_signature(secret: str, body: bytes, signature_header: str) -> bool:
    if not secret:
        return True
    if not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def should_process_pull_request(
    event: str,
    payload: dict[str, Any],
    ignore_users: set[str],
) -> tuple[bool, str]:
    if event != "pull_request":
        return False, f"event={event} not pull_request"
    action = payload.get("action", "")
    if action not in ("opened", "synchronize"):
        return False, f"action={action} not opened/synchronize"
    sender = payload.get("sender", {})
    login = sender.get("login", "")
    if login in ignore_users:
        return False, f"sender={login} in ignore list"
    if sender.get("type", "").lower() == "bot":
        return False, f"sender={login} is bot"
    return True, "ok"


# ---------------------------------------------------------------------------
# Review logic
# ---------------------------------------------------------------------------


def build_review_prompt(pr: dict[str, Any], diff: str) -> str:
    """Build review prompt from template, substituting PR metadata."""
    prompt_path = Path(__file__).parent.parent / "prompts" / "pr-review-prompt.md"
    template = (
        prompt_path.read_text()
        if prompt_path.exists()
        else ("Review PR #{pr_number}: {pr_title}\n{pr_body}\nDiff:\n{diff_text}")
    )
    # Truncate diff to avoid overwhelming the agent
    max_diff = 80000
    truncated_diff = diff[:max_diff]
    if len(diff) > max_diff:
        truncated_diff += f"\n\n... (diff truncated, {len(diff) - max_diff} chars omitted)"

    return template.format(
        pr_number=pr["number"],
        pr_title=pr["title"],
        pr_body=pr.get("body", "") or "(empty)",
        head_ref=pr.get("head", {}).get("ref", "unknown"),
        base_ref=pr.get("base", {}).get("ref", "main"),
        diff_text=truncated_diff,
    )


def fetch_diff(repo_path: str, pr_number: int, base_ref: str, head_ref: str) -> str:
    cwd = repo_path
    subprocess.run(
        ["git", "fetch", "origin", f"pull/{pr_number}/head:pr-{pr_number}"],
        cwd=cwd,
        capture_output=True,
        timeout=60,
    )
    result = subprocess.run(
        ["git", "diff", f"origin/{base_ref}...pr-{pr_number}"],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.stdout


def run_review(config: ReviewBotConfig, pr: dict[str, Any], diff: str) -> str:
    """Run Codex/Claude to review a PR. Agent posts review directly via gh CLI."""
    prompt = build_review_prompt(pr, diff)
    pr_num = pr["number"]
    run_id = uuid.uuid4().hex[:6]
    session_name = f"pr-review-{pr_num}-{run_id}"

    # Write prompt to temp file (avoid shell arg length limits)
    work_dir = Path(tempfile.mkdtemp(prefix=f"pr-review-{pr_num}-"))
    prompt_file = work_dir / "prompt.md"
    prompt_file.write_text(prompt, encoding="utf-8")

    # Agent reads prompt and posts review directly via gh CLI
    agent_task = (
        f"Read the PR review instructions from {prompt_file}. "
        f"Review the code carefully following the project standards. "
        f"Then post your review directly using gh pr review or gh pr comment. "
        f"The PR number is {pr_num}."
    )

    if config.review_agent == "codex":
        cmd = f"codex --dangerously-bypass-approvals-and-sandbox {shlex.quote(agent_task)}"
    else:
        cmd = f"claude --dangerously-skip-permissions {shlex.quote(agent_task)}"

    subprocess.run(
        ["tmux", "new-session", "-d", "-s", session_name, cmd],
        cwd=str(config.repo_path),
        timeout=10,
    )

    # Wait for agent to finish
    deadline = time.time() + config.review_timeout
    while time.time() < deadline:
        result = subprocess.run(
            ["tmux", "has-session", "-t", session_name],
            capture_output=True,
        )
        if result.returncode != 0:
            break
        time.sleep(15)

    timed_out = time.time() >= deadline

    # Capture tmux output to extract decision for notification
    output = subprocess.run(
        ["tmux", "capture-pane", "-t", session_name, "-p", "-S", "-500"],
        capture_output=True,
        text=True,
    )
    raw_output = output.stdout if output.returncode == 0 else ""

    # Cleanup
    subprocess.run(["tmux", "kill-session", "-t", session_name], capture_output=True)
    import shutil

    shutil.rmtree(work_dir, ignore_errors=True)

    if timed_out:
        LOG.warning("Review timed out for PR #%d", pr_num)
        return "(review timed out)"

    LOG.info("Agent finished review for PR #%d", pr_num)
    return raw_output


def extract_decision(raw_output: str) -> str:
    """Extract review decision from agent output."""
    if "request-changes" in raw_output.lower() or "REQUEST_CHANGES" in raw_output:
        return "REQUEST_CHANGES"
    if "--approve" in raw_output.lower() or "APPROVE" in raw_output:
        return "APPROVE"
    return "COMMENT"


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def notify_openclaw(pr_number: int, review_text: str, repo: str, decision: str) -> None:
    """Send notification to Feishu group chat via bot API to trigger OpenClaw processing loop."""
    import os
    import urllib.request

    chat_id = os.environ.get("PR_REVIEW_NOTIFY_CHAT", "")
    if not chat_id:
        LOG.info("PR_REVIEW_NOTIFY_CHAT not set, skipping notification")
        return

    app_id = os.environ.get("FEISHU_APP_ID", "")
    app_secret = os.environ.get("FEISHU_APP_SECRET", "")
    if not app_id or not app_secret:
        LOG.warning("FEISHU_APP_ID/FEISHU_APP_SECRET not set, skipping notification")
        return

    try:
        # Get tenant access token
        token_data = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode()
        token_req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            data=token_data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(token_req, timeout=10) as resp:
            token = json.loads(resp.read())["tenant_access_token"]

        # Build message
        short_review = review_text[:800]
        msg_content = (
            f"🔄 PR #{pr_number} 自动 Review 完成\n\n"
            f"仓库: {repo}\n"
            f"决策: {decision}\n\n"
            f"请根据 review findings 修复代码并 push，触发下一轮 review。\n\n"
            f"Review 摘要:\n{short_review}"
        )

        # Send to group chat
        send_data = json.dumps(
            {
                "receive_id": chat_id,
                "msg_type": "text",
                "content": json.dumps({"text": msg_content}),
            }
        ).encode()
        send_req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            data=send_data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        with urllib.request.urlopen(send_req, timeout=10) as resp:
            result = json.loads(resp.read())
            if result.get("code") == 0:
                LOG.info("Notified OpenClaw chat %s for PR #%d", chat_id, pr_number)
            else:
                LOG.warning("Feishu send failed: %s", result)
    except Exception:
        LOG.exception("Failed to notify OpenClaw for PR #%d", pr_number)


class ReviewOrchestrator:
    def __init__(self, config: ReviewBotConfig) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._active: dict[int, threading.Thread] = {}

    def submit_review(self, payload: dict[str, Any]) -> None:
        pr = payload["pull_request"]
        pr_num = pr["number"]
        repo = payload["repository"]["full_name"]

        with self._lock:
            old = self._active.pop(pr_num, None)
            if old and old.is_alive():
                LOG.info("Cancelling previous review for PR #%d", pr_num)

        def _worker() -> None:
            try:
                LOG.info("Starting review for PR #%d", pr_num)
                diff = fetch_diff(
                    self._config.repo_path,
                    pr_num,
                    pr["base"]["ref"],
                    pr["head"]["ref"],
                )
                if not diff.strip():
                    LOG.warning("Empty diff for PR #%d, skipping", pr_num)
                    return
                raw_output = run_review(self._config, pr, diff)
                decision = extract_decision(raw_output)
                LOG.info("Review decision for PR #%d: %s", pr_num, decision)
                notify_openclaw(pr_num, raw_output, repo, decision)
            except Exception:
                LOG.exception("Review failed for PR #%d", pr_num)

        t = threading.Thread(target=_worker, name=f"review-pr-{pr_num}", daemon=True)
        with self._lock:
            self._active[pr_num] = t
        t.start()


# ---------------------------------------------------------------------------
# ASGI app (Starlette for raw body access)
# ---------------------------------------------------------------------------


def create_app(config: ReviewBotConfig, orchestrator: ReviewOrchestrator) -> Any:
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def webhook(request: Request) -> JSONResponse:
        x_github_event = request.headers.get("x-github-event", "")
        x_hub_signature_256 = request.headers.get("x-hub-signature-256", "")
        body = await request.body()
        payload = json.loads(body.decode("utf-8"))

        # smee-client wraps the payload: {"payload": "<json string>", "x-github-event": "..."}
        if "payload" in payload and isinstance(payload.get("payload"), str):
            LOG.info("Detected smee-wrapped payload, unwrapping")
            inner_sig = payload.get("x-hub-signature-256", "")
            inner_event = payload.get("x-github-event", x_github_event)
            inner_body = payload["payload"].encode("utf-8")
            if inner_sig and config.webhook_secret:
                if not verify_webhook_signature(config.webhook_secret, inner_body, inner_sig):
                    return JSONResponse({"error": "invalid signature"}, status_code=401)
            x_github_event = inner_event
            payload = json.loads(payload["payload"])
        elif x_hub_signature_256 and config.webhook_secret:
            if not verify_webhook_signature(config.webhook_secret, body, x_hub_signature_256):
                return JSONResponse({"error": "invalid signature"}, status_code=401)
        ok, reason = should_process_pull_request(
            x_github_event,
            payload,
            config.ignore_users,
        )
        if not ok:
            return JSONResponse({"accepted": False, "reason": reason})

        orchestrator.submit_review(payload)
        return JSONResponse({"accepted": True, "reason": "scheduled"})

    return Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/webhook", webhook, methods=["POST"]),
        ]
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Run local GitHub PR review webhook server.")
    parser.add_argument("--dry-run", action="store_true", help="Validate imports/configuration and exit.")
    args = parser.parse_args()

    config = load_config()
    setup_logging(config.log_file)
    LOG.info(
        "Loaded config: agent=%s repo=%s timeout=%ss port=%s",
        config.review_agent,
        config.repo_path,
        config.review_timeout,
        config.port,
    )

    if args.dry_run:
        LOG.info("Dry run OK")
        return 0

    orchestrator = ReviewOrchestrator(config)
    app = create_app(config, orchestrator)
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError("uvicorn is required: uv sync --extra review-bot") from exc
    uvicorn.run(app, host="0.0.0.0", port=config.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
