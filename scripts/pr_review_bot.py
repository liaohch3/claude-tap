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

try:
    from scripts.pr_review_bot_config import ReviewBotConfig, load_config
except ModuleNotFoundError:  # pragma: no cover - direct script execution fallback
    from pr_review_bot_config import ReviewBotConfig, load_config

LOG = logging.getLogger("pr_review_bot")
PROMPT_TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "prompts" / "pr-review-prompt.md"


@dataclass
class ReviewWorker:
    thread: threading.Thread
    cancel_event: threading.Event
    run_id: str


def setup_logging(log_file: Path) -> None:
    LOG.setLevel(logging.INFO)
    if LOG.handlers:
        return

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)

    LOG.addHandler(stream_handler)
    LOG.addHandler(file_handler)


def verify_webhook_signature(secret: str, body: bytes, signature_header: str) -> bool:
    if not secret:
        return True
    if not signature_header.startswith("sha256="):
        return False
    provided = signature_header.split("=", 1)[1]
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided)


def should_process_pull_request(
    event_name: str,
    payload: dict[str, Any],
    ignore_users: set[str] | frozenset[str],
) -> tuple[bool, str]:
    if event_name != "pull_request":
        return False, "not a pull_request event"

    action = payload.get("action")
    if action not in {"opened", "synchronize"}:
        return False, f"ignored action={action}"

    sender_login = str(payload.get("sender", {}).get("login", ""))
    if sender_login in ignore_users or sender_login.endswith("[bot]"):
        return False, f"ignored sender={sender_login}"

    pr = payload.get("pull_request")
    if not isinstance(pr, dict) or "number" not in pr:
        return False, "missing pull_request payload"
    return True, "accepted"


def build_review_prompt(
    *,
    template_text: str,
    pr_number: int,
    pr_title: str,
    pr_body: str,
    head_ref: str,
    base_ref: str,
    diff_text: str,
) -> str:
    safe_body = pr_body.strip() or "(No PR description)"
    safe_diff = diff_text.strip() or "(No diff content)"
    return template_text.format(
        pr_number=pr_number,
        pr_title=pr_title.strip(),
        pr_body=safe_body,
        head_ref=head_ref.strip(),
        base_ref=base_ref.strip(),
        diff_text=safe_diff,
    )


class ReviewOrchestrator:
    def __init__(self, config: ReviewBotConfig) -> None:
        self.config = config
        self._workers: dict[int, ReviewWorker] = {}
        self._lock = threading.Lock()

    def submit_review(self, payload: dict[str, Any]) -> None:
        pr = payload["pull_request"]
        pr_number = int(pr["number"])
        run_id = uuid.uuid4().hex[:8]
        cancel_event = threading.Event()

        worker = ReviewWorker(
            thread=threading.Thread(
                target=self._review_pr_thread,
                args=(pr_number, payload, cancel_event, run_id),
                daemon=True,
            ),
            cancel_event=cancel_event,
            run_id=run_id,
        )

        with self._lock:
            previous = self._workers.get(pr_number)
            if previous:
                LOG.info("Cancelling previous worker for PR #%s run=%s", pr_number, previous.run_id)
                previous.cancel_event.set()
            self._workers[pr_number] = worker
        LOG.info("Starting worker for PR #%s run=%s", pr_number, run_id)
        worker.thread.start()

    def _review_pr_thread(
        self,
        pr_number: int,
        payload: dict[str, Any],
        cancel_event: threading.Event,
        run_id: str,
    ) -> None:
        try:
            review_text, recommendation = run_review_pipeline(
                config=self.config,
                pr_number=pr_number,
                payload=payload,
                cancel_event=cancel_event,
                run_id=run_id,
            )
            if cancel_event.is_set():
                LOG.info("Worker finished but was cancelled for PR #%s run=%s", pr_number, run_id)
                return
            post_review(self.config, pr_number=pr_number, review_text=review_text, recommendation=recommendation)
        except Exception:
            LOG.exception("Worker failed for PR #%s run=%s", pr_number, run_id)
        finally:
            with self._lock:
                current = self._workers.get(pr_number)
                if current and current.run_id == run_id:
                    self._workers.pop(pr_number, None)


def _run_git(repo_path: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _run_cmd(repo_path: Path, args: list[str]) -> str:
    result = subprocess.run(args, cwd=repo_path, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _agent_shell_command(agent: str, prompt_file: Path, output_file: Path, status_file: Path) -> str:
    if agent == "claude":
        runner = f"claude --print < {shlex.quote(str(prompt_file))}"
    else:
        runner = f"codex exec < {shlex.quote(str(prompt_file))}"
    return (
        f"set -euo pipefail; {runner} > {shlex.quote(str(output_file))} 2>&1; echo 0 > {shlex.quote(str(status_file))}"
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
            raise TimeoutError("review cancelled by newer event")
        if status_file.exists():
            return
        time.sleep(2)
    subprocess.run(["tmux", "kill-session", "-t", session_name], check=False, capture_output=True)
    raise TimeoutError(f"agent timeout after {timeout_seconds}s")


def run_review_pipeline(
    *,
    config: ReviewBotConfig,
    pr_number: int,
    payload: dict[str, Any],
    cancel_event: threading.Event,
    run_id: str,
) -> tuple[str, str]:
    repo_path = config.repo_path
    pr = payload["pull_request"]
    pr_title = str(pr.get("title", ""))
    pr_body = str(pr.get("body", ""))
    head_ref = str(pr.get("head", {}).get("ref", ""))
    base_ref = str(pr.get("base", {}).get("ref", "main"))

    LOG.info("PR #%s run=%s fetching branch", pr_number, run_id)
    _run_git(repo_path, ["fetch", "origin", f"pull/{pr_number}/head:pr-{pr_number}"])

    diff_text = _run_git(repo_path, ["diff", f"{base_ref}...pr-{pr_number}"])
    diff_path = Path(tempfile.gettempdir()) / f"pr-{pr_number}.diff"
    diff_path.write_text(diff_text, encoding="utf-8")

    template_text = PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")
    prompt = build_review_prompt(
        template_text=template_text,
        pr_number=pr_number,
        pr_title=pr_title,
        pr_body=pr_body,
        head_ref=head_ref,
        base_ref=base_ref,
        diff_text=diff_text,
    )

    run_tmp_dir = Path(tempfile.gettempdir()) / f"pr-review-bot-{pr_number}-{run_id}"
    run_tmp_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = run_tmp_dir / "prompt.md"
    output_file = run_tmp_dir / "review.txt"
    status_file = run_tmp_dir / "status.txt"
    prompt_file.write_text(prompt, encoding="utf-8")

    session_name = f"pr-review-{pr_number}-{run_id}"
    command = _agent_shell_command(config.review_agent, prompt_file, output_file, status_file)
    LOG.info("PR #%s run=%s starting tmux session=%s", pr_number, run_id, session_name)
    tmux_result = subprocess.run(
        ["tmux", "new-session", "-d", "-s", session_name, command],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if tmux_result.returncode != 0:
        raise RuntimeError(f"tmux session start failed: {tmux_result.stderr.strip()}")

    _wait_for_agent(
        session_name=session_name,
        status_file=status_file,
        timeout_seconds=config.review_timeout,
        cancel_event=cancel_event,
    )
    subprocess.run(["tmux", "kill-session", "-t", session_name], check=False, capture_output=True)

    review_text = output_file.read_text(encoding="utf-8").strip()
    if not review_text:
        review_text = "Review agent returned empty output."
    review_text = review_text[:65000]
    recommendation = parse_recommendation(review_text)
    return review_text, recommendation


def parse_recommendation(review_text: str) -> str:
    upper = review_text.upper()
    if "REQUEST_CHANGES" in upper:
        return "REQUEST_CHANGES"
    if "APPROVE" in upper:
        return "APPROVE"
    return "COMMENT"


def post_review(config: ReviewBotConfig, *, pr_number: int, review_text: str, recommendation: str) -> None:
    repo_path = config.repo_path
    temp_path = Path(tempfile.gettempdir()) / f"pr-{pr_number}-review-body.txt"
    temp_path.write_text(review_text, encoding="utf-8")
    LOG.info("Posting %s review for PR #%s", recommendation, pr_number)

    if recommendation == "APPROVE":
        _run_cmd(
            repo_path,
            [
                "gh",
                "pr",
                "review",
                str(pr_number),
                "--approve",
                "--body-file",
                str(temp_path),
            ],
        )
        return

    if recommendation == "REQUEST_CHANGES":
        _run_cmd(
            repo_path,
            [
                "gh",
                "pr",
                "review",
                str(pr_number),
                "--request-changes",
                "--body-file",
                str(temp_path),
            ],
        )
        return

    _run_cmd(
        repo_path,
        [
            "gh",
            "pr",
            "comment",
            str(pr_number),
            "--body-file",
            str(temp_path),
        ],
    )


def create_app(config: ReviewBotConfig, orchestrator: ReviewOrchestrator) -> Any:
    try:
        from fastapi import FastAPI, Header, HTTPException, Request
    except ImportError as exc:
        raise RuntimeError("FastAPI is required. Install with: uv sync --extra review-bot") from exc

    app = FastAPI(title="Local PR Review Bot")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhook")
    async def webhook(
        request: Request,
        x_github_event: str = Header(default=""),
        x_hub_signature_256: str = Header(default=""),
    ) -> dict[str, Any]:
        body = await request.body()
        if not verify_webhook_signature(config.webhook_secret, body, x_hub_signature_256):
            raise HTTPException(status_code=401, detail="invalid signature")

        payload = json.loads(body.decode("utf-8"))
        ok, reason = should_process_pull_request(
            x_github_event,
            payload,
            config.ignore_users,
        )
        if not ok:
            return {"accepted": False, "reason": reason}

        orchestrator.submit_review(payload)
        return {"accepted": True, "reason": "scheduled"}

    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run local GitHub PR review webhook server.")
    parser.add_argument("--dry-run", action="store_true", help="Validate imports/configuration and exit.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
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
        raise RuntimeError("uvicorn is required. Install with: uv sync --extra review-bot") from exc
    uvicorn.run(app, host="0.0.0.0", port=config.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
