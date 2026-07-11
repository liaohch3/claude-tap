#!/usr/bin/env bash
set -euo pipefail

# Run a real non--print Claude session under tmux, send two prompts, and collect artifacts.
# Usage:
#   scripts/run_real_e2e_tmux.sh
# Env:
#   PROMPT_ONE / PROMPT_TWO override default prompts
#   PERMISSION_MODE override Claude permission mode (default: bypassPermissions)
#   SUBMIT_KEY override tmux submit key (default: Enter)

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if ! command -v tmux >/dev/null 2>&1; then
  echo "error: tmux not found. Install it first (e.g. brew install tmux)." >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "error: uv not found." >&2
  exit 1
fi

TS="$(date +%s)"
TRACE_DIR="/tmp/ctap-tmux-simple-$TS"
SESSION="ctap_simple_$TS"
PANE_LOG="/tmp/claude-tap-recordings/tmux-simple-$TS.log"
PERMISSION_MODE="${PERMISSION_MODE:-bypassPermissions}"
PROMPT_ONE="${PROMPT_ONE:-Use the shell tool to run command ls in the current directory, then reply with any 5 filenames only.}"
PROMPT_TWO="${PROMPT_TWO:-Thank you.}"
SUBMIT_KEY="${SUBMIT_KEY:-Enter}"

mkdir -p "$TRACE_DIR" /tmp/claude-tap-recordings

wait_for_prompt() {
  local timeout="${1:-60}"
  local i=0
  while (( i < timeout )); do
    if tmux capture-pane -p -S -300 -t "$SESSION" 2>/dev/null | grep -qF "❯"; then
      return 0
    fi
    sleep 1
    ((i += 1))
  done
  return 1
}

wait_for_prompt_in_trace() {
  local needle="$1"
  local timeout="${2:-120}"
  local i=0
  while (( i < timeout )); do
    local jf
    jf="$(ls -1 "$TRACE_DIR"/trace_*.jsonl 2>/dev/null | tail -n1 || true)"
    if [[ -n "$jf" ]] && grep -qF "$needle" "$jf"; then
      return 0
    fi
    sleep 1
    ((i += 1))
  done
  return 1
}

send_prompt() {
  local text="$1"
  local key="$2"
  tmux send-keys -t "$SESSION" -l "$text"
  # Give the TUI a moment to consume the literal input before submit.
  sleep 0.2
  tmux send-keys -t "$SESSION" "$key"
}

submit_prompt_with_detection() {
  local text="$1"
  local timeout="${2:-120}"
  local attempt
  local max_attempts=3

  for ((attempt = 1; attempt <= max_attempts; attempt++)); do
    send_prompt "$text" "$SUBMIT_KEY"
    if wait_for_prompt_in_trace "$text" "$timeout"; then
      echo "submit_key=$SUBMIT_KEY attempt=$attempt"
      return 0
    fi
    # If submit did not fire, clear line and retry the same key.
    tmux send-keys -t "$SESSION" Escape C-u
    sleep 1
  done
  return 1
}

cleanup() {
  tmux capture-pane -S -6000 -p -t "$SESSION" > "$PANE_LOG" 2>/dev/null || true
  tmux kill-session -t "$SESSION" >/dev/null 2>&1 || true
}
trap cleanup EXIT

tmux new-session -d -s "$SESSION" -x 160 -y 46
tmux send-keys -t "$SESSION" -l "cd $REPO_ROOT && uv run python -m claude_tap --tap-output-dir $TRACE_DIR --tap-proxy-mode forward -- --permission-mode $PERMISSION_MODE"
tmux send-keys -t "$SESSION" Enter

wait_for_prompt 90 || true

submit_prompt_with_detection "$PROMPT_ONE" 30 || {
  echo "error: prompt one not observed in trace jsonl" >&2
  exit 2
}
wait_for_prompt 120 || true

submit_prompt_with_detection "$PROMPT_TWO" 30 || {
  echo "error: prompt two not observed in trace jsonl" >&2
  exit 3
}
wait_for_prompt 90 || true

tmux send-keys -t "$SESSION" -l "/exit"
tmux send-keys -t "$SESSION" Enter
sleep 2

JSONL="$(ls -1 "$TRACE_DIR"/trace_*.jsonl | tail -n1)"
HTML="${JSONL%.jsonl}.html"
if [[ ! -f "$HTML" ]]; then
  uv run python - "$JSONL" "$HTML" <<'PY'
import sys
from pathlib import Path
from claude_tap import _generate_html_viewer

_generate_html_viewer(Path(sys.argv[1]), Path(sys.argv[2]))
PY
fi

uv run python - "$JSONL" "$PROMPT_ONE" "$PROMPT_TWO" <<'PY'
import json
import sys
from pathlib import Path

jf = Path(sys.argv[1])
p1 = sys.argv[2]
p2 = sys.argv[3]

text = jf.read_text(encoding="utf-8")
if p1 not in text:
    raise SystemExit("assert failed: prompt one missing in jsonl")
if p2 not in text:
    raise SystemExit("assert failed: prompt two missing in jsonl")

records = [json.loads(x) for x in text.splitlines() if x.strip()]
msg = [r for r in records if r.get("request", {}).get("path", "").startswith("/v1/messages")]
if len(msg) < 2:
    raise SystemExit(f"assert failed: expected >=2 /v1/messages requests, got {len(msg)}")
print(f"assert ok: message_requests={len(msg)}")
PY

echo "TRACE_DIR=$TRACE_DIR"
echo "JSONL=$JSONL"
echo "HTML=$HTML"
echo "PANE_LOG=$PANE_LOG"
