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
    template = prompt_path.read_text() if prompt_path.exists() else (
        "Review PR #{pr_number}: {pr_title}\n{pr_body}\nDiff:\n{diff_text}"
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
        cwd=cwd, capture_output=True, timeout=60,
    )
    result = subprocess.run(
        ["git", "diff", f"origin/{base_ref}...pr-{pr_number}"],
        cwd=cwd, capture_output=True, text=True, timeout=60,
    )
    return result.stdout


def run_review(config: ReviewBotConfig, pr: dict[str, Any], diff: str) -> str:
    """Run Codex/Claude to review a PR. Writes prompt to file, captures output from file."""
    prompt = build_review_prompt(pr, diff)
    pr_num = pr["number"]
    run_id = uuid.uuid4().hex[:6]
    session_name = f"pr-review-{pr_num}-{run_id}"

    # Write prompt and diff to temp files (avoid shell arg length limits)
    work_dir = Path(tempfile.mkdtemp(prefix=f"pr-review-{pr_num}-"))
    prompt_file = work_dir / "prompt.md"
    output_file = work_dir / "review-output.md"
    prompt_file.write_text(prompt, encoding="utf-8")

    # Instruct agent to read prompt from file and write review to output file
    agent_task = (
        f"Read the PR review prompt from {prompt_file}. "
        f"Follow the instructions exactly. "
        f"After completing the review, write ONLY the final review text "
        f"(Summary + Findings + Suggested decision) to {output_file}. "
        f"Do not include any other content in that file."
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

    deadline = time.time() + config.review_timeout
    while time.time() < deadline:
        result = subprocess.run(
            ["tmux", "has-session", "-t", session_name],
            capture_output=True,
        )
        if result.returncode != 0:
            break
        # Also check if output file exists (agent finished writing)
        if output_file.exists() and output_file.stat().st_size > 100:
            LOG.info("Review output file detected for PR #%d", pr_num)
            time.sleep(5)  # Give agent a moment to finish writing
            break
        time.sleep(15)

    # Try to read from output file first (clean output)
    review_text = ""
    if output_file.exists():
        review_text = output_file.read_text(encoding="utf-8").strip()
        LOG.info("Read review from output file (%d chars) for PR #%d", len(review_text), pr_num)

    # Fallback: extract from tmux capture if output file is empty
    if not review_text or len(review_text) < 50:
        LOG.warning("Output file empty/missing for PR #%d, falling back to tmux capture", pr_num)
        output = subprocess.run(
            ["tmux", "capture-pane", "-t", session_name, "-p", "-S", "-500"],
            capture_output=True, text=True,
        )
        if output.returncode == 0:
            review_text = _extract_review_from_tmux(output.stdout)

    # Cleanup
    subprocess.run(["tmux", "kill-session", "-t", session_name], capture_output=True)
    import shutil
    shutil.rmtree(work_dir, ignore_errors=True)

    return review_text or "(review 输出为空，请检查 agent 日志)"


def _extract_review_from_tmux(raw: str) -> str:
    """Extract the review section from raw tmux output."""
    lines = raw.split("\n")
    # Look for Summary section start
    review_lines: list[str] = []
    capturing = False
    for line in lines:
        stripped = line.strip()
        # Start capturing at "Summary" or "## Summary"
        if not capturing and ("Summary" in stripped and len(stripped) < 50):
            capturing = True
        if capturing:
            # Stop at Codex/Claude UI elements
            if stripped.startswith("›") or "gpt-5" in stripped or "esc to interrupt" in stripped:
                continue
            review_lines.append(line)
    if review_lines:
        return "\n".join(review_lines).strip()
    # If no structured output found, return last 100 meaningful lines
    meaningful = [l for l in lines if l.strip() and not l.strip().startswith("•") and "Working" not in l]
    return "\n".join(meaningful[-100:]).strip()


def post_review(pr_number: int, review_text: str, repo: str, agent: str = "codex") -> None:
    """Post formatted review comment to GitHub PR."""
    # Format as a clean GitHub comment
    body = (
        f"## 🤖 自动 Code Review\n\n"
        f"> 由本地 {agent.capitalize()} 自动生成\n\n"
        f"---\n\n"
        f"{review_text}\n\n"
        f"---\n"
        f"*⚡ Powered by local PR Review Bot*"
    )
    # Write to temp file to avoid shell arg length issues
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(body)
        body_file = f.name
    try:
        subprocess.run(
            ["gh", "pr", "comment", str(pr_number), "--repo", repo, "--body-file", body_file],
            timeout=30,
        )
        LOG.info("Posted review to PR #%d", pr_number)
    finally:
        Path(body_file).unlink(missing_ok=True)


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
                post_review(pr_num, review, repo, self._config.review_agent)
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
