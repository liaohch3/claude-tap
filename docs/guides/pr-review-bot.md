# Local PR Auto-Review Bot Guide

This guide explains how to run the webhook-driven PR review bot locally and automatically post review results back to a GitHub PR.

## 1. Prerequisites

- Python 3.11+
- `uv`
- `git`
- `gh` (authenticated with review/comment permissions for the target repository)
- `tmux`
- At least one review agent CLI:
  - `codex` (default)
  - `claude`

Optional:
- `npx` (for starting `smee-client` automatically)

## 2. Install And Configure

Run from the repository root:

```bash
uv sync --extra review-bot --dev
```

Set environment variables:

```bash
export PR_REVIEW_WEBHOOK_SECRET="your_webhook_secret"
export PR_REVIEW_REPO_PATH="/absolute/path/to/this/repo"
export PR_REVIEW_AGENT="codex"   # or claude
export PR_REVIEW_OUTPUT_LANGUAGE="en"  # zh or en
export PR_REVIEW_TIMEOUT="600"
export PR_REVIEW_PORT="3456"
```

Optional variables:

```bash
export PR_REVIEW_IGNORE_USERS="github-actions[bot],dependabot[bot]"
export PR_REVIEW_LOG_FILE="/tmp/pr-review-bot.log"
export PR_REVIEW_ALLOW_INSECURE_WEBHOOKS="false"  # local debug only
export SMEE_URL="https://smee.io/your-channel"
```

## 3. Configure GitHub Webhook

Create a webhook in the target GitHub repository:

- Payload URL:
  - Direct local endpoint: `http://<your-host>:3456/webhook`
  - With smee relay: `https://smee.io/<your-channel>`
- Content type: `application/json`
- Secret: same value as `PR_REVIEW_WEBHOOK_SECRET`
- Events: `Pull requests`

The bot only processes these actions:
- `opened`
- `synchronize`

## 4. Start The Bot

Foreground:

```bash
scripts/start_review_bot.sh
```

Background:

```bash
scripts/start_review_bot.sh --daemon
```

Manual dry-run (validate imports and configuration):

```bash
python3 scripts/pr_review_bot.py --dry-run
```

Health check:

```bash
curl -s http://127.0.0.1:${PR_REVIEW_PORT:-3456}/health
```

## 5. Runtime Flow

After receiving a PR webhook, the bot:

1. Verifies `X-Hub-Signature-256` (or wrapped signature for smee payloads).
2. Filters out non-`pull_request` events and actions other than `opened`/`synchronize`.
3. Ignores configured bot/service accounts to avoid loops.
4. Cancels any previous in-flight review for the same PR.
5. Runs:
   - `git fetch origin +pull/<number>/head:pr-<number>`
   - `git fetch origin <base>`
   - `git diff origin/<base>...pr-<number>`
6. Builds the review prompt from `prompts/pr-review-prompt.md`.
7. Runs `codex` or `claude` in a tmux session.
8. Applies timeout and cancellation handling.
9. Parses recommendation from model output and posts:
   - `APPROVE` -> `gh pr review --approve`
   - `REQUEST_CHANGES` -> `gh pr review --request-changes`
   - otherwise -> `gh pr comment`

## 6. Logs And Troubleshooting

Default log files:

- `/tmp/pr-review-bot.log`
- `/tmp/pr-review-bot-stdout.log` (daemon mode)
- `/tmp/pr-review-bot-smee.log` (when smee relay is enabled)

Common issues:

1. Webhook returns 401.
   Verify `PR_REVIEW_WEBHOOK_SECRET` matches the GitHub webhook secret.

2. No PR review/comment is posted.
   Run `gh auth status` and verify token permissions for PR reviews/comments.

3. Agent command fails.
   Verify `codex` or `claude` CLI works directly in the shell.

4. tmux errors.
   Install tmux and verify with `tmux -V`.

5. smee relay is not forwarding.
   Verify `SMEE_URL` and test `npx smee-client` manually.
