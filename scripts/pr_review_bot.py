#!/usr/bin/env python3
"""Webhook-driven local PR review bot."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from scripts.pr_review_bot_config import ReviewBotConfig, load_config

LOG = logging.getLogger("pr-review-bot")


def setup_logging(log_file: Path) -> None:
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
        return False
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def should_process_pull_request(
    event: str,
    payload: dict[str, Any],
    ignore_users: frozenset[str],
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


def build_review_prompt(pr: dict[str, Any], diff: str, output_language: str) -> str:
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
        output_language=output_language,
        diff_text=truncated_diff,
    )


def fetch_diff(repo_path: Path, pr_number: int, base_ref: str) -> str:
    cwd = str(repo_path)
    subprocess.run(
        ["git", "fetch", "origin", f"+pull/{pr_number}/head:pr-{pr_number}"],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    subprocess.run(
        ["git", "fetch", "origin", base_ref],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    result = subprocess.run(
        ["git", "diff", f"origin/{base_ref}...pr-{pr_number}"],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return result.stdout


def _agent_shell_command(agent: str, agent_task: str, output_file: Path, status_file: Path) -> str:
    if agent == "claude":
        runner = f"claude --dangerously-skip-permissions {shlex.quote(agent_task)}"
    else:
        runner = f"codex --dangerously-bypass-approvals-and-sandbox {shlex.quote(agent_task)}"
    return (
        "set -uo pipefail; "
        f"{runner} > {shlex.quote(str(output_file))} 2>&1; "
        f"status=$?; printf '%s' \"$status\" > {shlex.quote(str(status_file))}; "
        'exit "$status"'
    )


def _wait_for_agent(
    *,
    session_name: str,
    status_file: Path,
    timeout_seconds: int,
    cancel_event: threading.Event,
) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if cancel_event.is_set():
            subprocess.run(["tmux", "kill-session", "-t", session_name], check=False, capture_output=True)
            raise TimeoutError("review cancelled by newer event")
        if status_file.exists():
            status = status_file.read_text(encoding="utf-8").strip() or "1"
            if status != "0":
                raise RuntimeError(f"agent exited with status {status}")
            return
        tmux_state = subprocess.run(
            ["tmux", "has-session", "-t", session_name],
            capture_output=True,
        )
        if tmux_state.returncode != 0:
            raise RuntimeError("agent session terminated unexpectedly before reporting status")
        time.sleep(2)
    subprocess.run(["tmux", "kill-session", "-t", session_name], check=False, capture_output=True)
    raise TimeoutError(f"agent timeout after {timeout_seconds}s")


def run_review(
    config: ReviewBotConfig,
    pr: dict[str, Any],
    diff: str,
    cancel_event: threading.Event,
) -> str:
    """Run Codex/Claude to review a PR. Agent posts review directly via gh CLI."""
    prompt = build_review_prompt(pr, diff, config.output_language)
    pr_num = pr["number"]
    run_id = uuid.uuid4().hex[:6]
    session_name = f"pr-review-{pr_num}-{run_id}"

    # Write prompt to temp file (avoid shell arg length limits)
    work_dir = Path(tempfile.mkdtemp(prefix=f"pr-review-{pr_num}-"))
    prompt_file = work_dir / "prompt.md"
    output_file = work_dir / "agent-output.log"
    status_file = work_dir / "agent-status.txt"
    prompt_file.write_text(prompt, encoding="utf-8")

    # Agent reads prompt and posts review directly via gh CLI
    agent_task = (
        f"Read the PR review instructions from {prompt_file}. "
        f"Review the code carefully following the project standards. "
        f"Then post your review directly using gh pr review or gh pr comment. "
        f"The PR number is {pr_num}."
    )

    cmd = _agent_shell_command(config.review_agent, agent_task, output_file, status_file)

    subprocess.run(
        ["tmux", "new-session", "-d", "-s", session_name, cmd],
        cwd=str(config.repo_path),
        timeout=10,
        check=True,
    )

    try:
        _wait_for_agent(
            session_name=session_name,
            status_file=status_file,
            timeout_seconds=config.review_timeout,
            cancel_event=cancel_event,
        )
        raw_output = output_file.read_text(encoding="utf-8") if output_file.exists() else ""
        LOG.info("Agent finished review for PR #%d", pr_num)
        return raw_output
    except TimeoutError:
        if cancel_event.is_set():
            raise
        LOG.warning("Review timed out for PR #%d", pr_num)
        return "(review timed out)"
    finally:
        subprocess.run(["tmux", "kill-session", "-t", session_name], check=False, capture_output=True)
        shutil.rmtree(work_dir, ignore_errors=True)


def extract_decision(raw_output: str) -> str:
    """Extract review decision from agent output."""
    for line in raw_output.splitlines():
        normalized = line.strip().upper()
        if normalized.startswith("DECISION:") or normalized.startswith("SUGGESTED DECISION:"):
            if re.search(r"\bREQUEST_CHANGES\b", normalized):
                return "REQUEST_CHANGES"
            if re.search(r"\bAPPROVE\b", normalized):
                return "APPROVE"
            if re.search(r"\bCOMMENT\b", normalized):
                return "COMMENT"
    if re.search(r"\bgh\s+pr\s+review\b[^\n]*\s--request-changes\b", raw_output, re.IGNORECASE):
        return "REQUEST_CHANGES"
    if re.search(r"\bgh\s+pr\s+review\b[^\n]*\s--approve\b", raw_output, re.IGNORECASE):
        return "APPROVE"
    if re.search(r"\bgh\s+pr\s+comment\b", raw_output, re.IGNORECASE):
        return "COMMENT"
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
            raw_token_body = resp.read().decode("utf-8")
            if resp.getcode() != 200:
                LOG.warning("Feishu token request HTTP %s: %s", resp.getcode(), raw_token_body)
                return
            try:
                token_resp = json.loads(raw_token_body)
            except json.JSONDecodeError:
                LOG.warning("Feishu token response is not valid JSON: %s", raw_token_body)
                return
            token = token_resp.get("tenant_access_token")
            if token_resp.get("code") != 0 or not token:
                LOG.warning("Feishu token request failed: %s", raw_token_body)
                return

        # Build message
        short_review = review_text[:800]
        msg_content = (
            f"PR #{pr_number} automated review completed.\n\n"
            f"Repository: {repo}\n"
            f"Decision: {decision}\n\n"
            "Please apply fixes from the review findings and push updates for the next cycle.\n\n"
            f"Review summary:\n{short_review}"
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
            raw_send_body = resp.read().decode("utf-8")
            try:
                result = json.loads(raw_send_body)
            except json.JSONDecodeError:
                LOG.warning("Feishu send response is not valid JSON: %s", raw_send_body)
                return
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
        self._cancel: dict[int, threading.Event] = {}

    def submit_review(self, payload: dict[str, Any]) -> None:
        pr = payload["pull_request"]
        pr_num = pr["number"]
        repo = payload["repository"]["full_name"]

        with self._lock:
            old = self._active.pop(pr_num, None)
            old_cancel = self._cancel.pop(pr_num, None)
            if old and old.is_alive():
                LOG.info("Cancelling previous review for PR #%d", pr_num)
                if old_cancel:
                    old_cancel.set()
        if old and old.is_alive():
            old.join(timeout=5)
            if old.is_alive():
                LOG.warning("Previous review thread still running for PR #%d", pr_num)

        cancel_event = threading.Event()

        def _worker() -> None:
            try:
                LOG.info("Starting review for PR #%d", pr_num)
                diff = fetch_diff(
                    self._config.repo_path,
                    pr_num,
                    pr["base"]["ref"],
                )
                if not diff.strip():
                    LOG.warning("Empty diff for PR #%d, skipping", pr_num)
                    return
                raw_output = run_review(self._config, pr, diff, cancel_event)
                if cancel_event.is_set():
                    LOG.info("Skipping notification for cancelled review PR #%d", pr_num)
                    return
                decision = extract_decision(raw_output)
                LOG.info("Review decision for PR #%d: %s", pr_num, decision)
                notify_openclaw(pr_num, raw_output, repo, decision)
            except TimeoutError:
                LOG.info("Review cancelled for PR #%d", pr_num)
            except Exception:
                LOG.exception("Review failed for PR #%d", pr_num)
            finally:
                with self._lock:
                    if self._active.get(pr_num) is threading.current_thread():
                        self._active.pop(pr_num, None)
                        self._cancel.pop(pr_num, None)

        t = threading.Thread(target=_worker, name=f"review-pr-{pr_num}", daemon=True)
        with self._lock:
            self._active[pr_num] = t
            self._cancel[pr_num] = cancel_event
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
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            return JSONResponse({"error": "invalid json payload"}, status_code=400)

        # smee-client wraps the payload: {"payload": "<json string>", "x-github-event": "..."}
        if "payload" in payload and isinstance(payload.get("payload"), str):
            LOG.info("Detected smee-wrapped payload, unwrapping")
            inner_sig = payload.get("x-hub-signature-256", "")
            inner_event = payload.get("x-github-event", x_github_event)
            inner_body = payload["payload"].encode("utf-8")
            if config.webhook_secret and not inner_sig:
                return JSONResponse({"error": "missing signature"}, status_code=401)
            if config.webhook_secret:
                if not verify_webhook_signature(config.webhook_secret, inner_body, inner_sig):
                    return JSONResponse({"error": "invalid signature"}, status_code=401)
            x_github_event = inner_event
            try:
                payload = json.loads(payload["payload"])
            except json.JSONDecodeError:
                return JSONResponse({"error": "invalid json payload"}, status_code=400)
        elif config.webhook_secret:
            if not x_hub_signature_256:
                return JSONResponse({"error": "missing signature"}, status_code=401)
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
    if not args.dry_run and not config.webhook_secret and not config.allow_insecure_webhooks:
        raise RuntimeError("PR_REVIEW_WEBHOOK_SECRET is required unless PR_REVIEW_ALLOW_INSECURE_WEBHOOKS is set")
    LOG.info(
        "Loaded config: agent=%s repo=%s timeout=%ss port=%s output_language=%s insecure_webhooks=%s",
        config.review_agent,
        config.repo_path,
        config.review_timeout,
        config.port,
        config.output_language,
        config.allow_insecure_webhooks,
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
