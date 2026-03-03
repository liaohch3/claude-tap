#!/bin/sh
set -eu

usage() {
  cat <<'EOF'
Usage: scripts/start_review_bot.sh [--daemon]

Environment:
  PR_REVIEW_PORT            Webhook server port (default: 3456)
  PR_REVIEW_REPO_PATH       Repository path for git/gh commands
  PR_REVIEW_WEBHOOK_SECRET  GitHub webhook secret
  PR_REVIEW_AGENT           codex or claude (default: codex)
  SMEE_URL                  Optional smee relay URL
EOF
}

daemon=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --daemon)
      daemon=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

port="${PR_REVIEW_PORT:-3456}"
repo_path="${PR_REVIEW_REPO_PATH:-$(pwd)}"
mkdir -p /tmp

echo "Installing review bot dependencies..."
uv sync --extra review-bot --dev

smee_pid=""
if [ -n "${SMEE_URL:-}" ]; then
  echo "Starting smee relay from ${SMEE_URL} to http://127.0.0.1:${port}/webhook"
  if command -v npx >/dev/null 2>&1; then
    npx smee-client --url "$SMEE_URL" --path /webhook --target "http://127.0.0.1:${port}" >/tmp/pr-review-bot-smee.log 2>&1 &
    smee_pid=$!
  else
    echo "warning: npx not found; skipping smee relay startup" >&2
  fi
fi

if [ "$daemon" -eq 1 ]; then
  echo "Starting review bot in daemon mode on port ${port}"
  nohup python3 scripts/pr_review_bot.py >/tmp/pr-review-bot-stdout.log 2>&1 &
  bot_pid=$!
  echo "$bot_pid" >/tmp/pr-review-bot.pid
  if [ -n "$smee_pid" ]; then
    echo "$smee_pid" >/tmp/pr-review-bot-smee.pid
  fi
  echo "Bot PID: $bot_pid"
  exit 0
fi

cd "$repo_path"
python3 scripts/pr_review_bot.py
