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
    prompt_path = Path(__file__).parent.parent / "prompts" / "pr-review-prompt.md"
    template = prompt_path.read_text() if prompt_path.exists() else "Review this PR diff."
    return (
        f"{template}\n\n"
        f"## PR #{pr['number']}: {pr['title']}\n\n"
        f"### Description\n{pr.get('body', '') or '(empty)'}\n\n"
        f"### Diff\n```diff\n{diff[:50000]}\n```\n"
    )


def fetch_diff(repo_path: str, pr_number: int, base_ref: str, head_ref: str) -> str:
    cwd = repo_path
    subprocess.run(
        ["git", "fetch", "origin", f"pull/{pr_number}/head:pr-{pr_number}"],
        cwd=cwd, capture_output=True, timeout=60,
    )
    result = subprocess.run(
        ["git", "diff", f"origin/{base_ref}...pr-{pr_number}"],
        cwd=cwd, capture_output=True, text=True, timeout=60,
    )
    return result.stdout


def run_review(config: ReviewBotConfig, pr: dict[str, Any], diff: str) -> str:
    prompt = build_review_prompt(pr, diff)
    pr_num = pr["number"]
    session_name = f"pr-review-{pr_num}-{uuid.uuid4().hex[:6]}"

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(prompt)
        prompt_file = f.name

    if config.review_agent == "codex":
        cmd = f'codex --dangerously-bypass-approvals-and-sandbox "$(cat {shlex.quote(prompt_file)})"'
    else:
        cmd = f'claude --dangerously-skip-permissions "$(cat {shlex.quote(prompt_file)})"'

    subprocess.run(
        ["tmux", "new-session", "-d", "-s", session_name, cmd],
        timeout=10,
    )

    deadline = time.time() + config.review_timeout
    while time.time() < deadline:
        result = subprocess.run(
            ["tmux", "has-session", "-t", session_name],
            capture_output=True,
        )
        if result.returncode != 0:
            break
        time.sleep(15)

    output = subprocess.run(
        ["tmux", "capture-pane", "-t", session_name, "-p", "-S", "-200"],
        capture_output=True, text=True,
    )
    review_text = output.stdout if output.returncode == 0 else "(review capture failed)"

    subprocess.run(["tmux", "kill-session", "-t", session_name], capture_output=True)
    Path(prompt_file).unlink(missing_ok=True)
    return review_text


def post_review(pr_number: int, review_text: str, repo: str) -> None:
    body = f"🤖 **自动 Review（本地 Codex）**\n\n{review_text}"
    subprocess.run(
        ["gh", "pr", "comment", str(pr_number), "--repo", repo, "--body", body],
        timeout=30,
    )
    LOG.info("Posted review to PR #%d", pr_number)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

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
                review = run_review(self._config, pr, diff)
                post_review(pr_num, review, repo)
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

    return Starlette(routes=[
        Route("/health", health, methods=["GET"]),
        Route("/webhook", webhook, methods=["POST"]),
    ])


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
