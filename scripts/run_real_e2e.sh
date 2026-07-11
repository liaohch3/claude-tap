#!/usr/bin/env bash
set -euo pipefail

# Run a real Claude E2E flow through claude-tap forward proxy mode without tmux.
# Usage:
#   scripts/run_real_e2e.sh
# Env:
#   PROMPT_ONE / PROMPT_TWO override default prompts.
#   CLAUDE_ARGS extra claude CLI args (e.g. "--model sonnet").

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if ! command -v uv >/dev/null 2>&1; then
  echo "error: uv not found." >&2
  exit 1
fi

if ! command -v claude >/dev/null 2>&1; then
  echo "error: claude CLI not found." >&2
  exit 1
fi

TS="$(date +%s)"
TRACE_DIR="/tmp/ctap-real-e2e-$TS"
RUN_LOG="/tmp/claude-tap-recordings/real-e2e-$TS.log"
PROMPT_ONE="${PROMPT_ONE:-Reply with exactly: REAL_E2E_TURN_ONE_OK}"
PROMPT_TWO="${PROMPT_TWO:-What was the exact text I asked you to reply with in the previous turn?}"
CLAUDE_ARGS="${CLAUDE_ARGS:-}"

mkdir -p "$TRACE_DIR" /tmp/claude-tap-recordings

run_turn() {
  local prompt="$1"
  shift
  local extra_args=("$@")

  local claude_args=()
  if [[ -n "$CLAUDE_ARGS" ]]; then
    # shellcheck disable=SC2206
    claude_args=($CLAUDE_ARGS)
  fi
  local cmd=(
    uv run python -m claude_tap
    --tap-output-dir "$TRACE_DIR"
    --tap-proxy-mode forward
    --
  )
  if [[ ${#claude_args[@]} -gt 0 ]]; then
    cmd+=("${claude_args[@]}")
  fi
  cmd+=(-p "$prompt")
  if [[ ${#extra_args[@]} -gt 0 ]]; then
    cmd+=("${extra_args[@]}")
  fi

  echo "==> ${cmd[*]}" | tee -a "$RUN_LOG"
  "${cmd[@]}" 2>&1 | tee -a "$RUN_LOG"
}

run_turn "$PROMPT_ONE"
run_turn "$PROMPT_TWO" -c

uv run python - "$TRACE_DIR" "$PROMPT_ONE" "$PROMPT_TWO" <<'PY'
import json
import sys
from pathlib import Path

from claude_tap import _generate_html_viewer

trace_dir = Path(sys.argv[1])
p1 = sys.argv[2]
p2 = sys.argv[3]

jsonl_files = sorted(trace_dir.glob("trace_*.jsonl"))
if not jsonl_files:
    raise SystemExit(f"assert failed: no trace_*.jsonl found in {trace_dir}")

all_text_parts: list[str] = []
message_count = 0

for jf in jsonl_files:
    text = jf.read_text(encoding="utf-8")
    if text:
        all_text_parts.append(text)
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            path = rec.get("request", {}).get("path", "")
            if isinstance(path, str) and path.startswith("/v1/messages"):
                message_count += 1

    html = jf.with_suffix(".html")
    if not html.exists():
        _generate_html_viewer(jf, html)

all_text = "\n".join(all_text_parts)
if p1 not in all_text:
    raise SystemExit("assert failed: prompt one missing in trace jsonl files")
if p2 not in all_text:
    raise SystemExit("assert failed: prompt two missing in trace jsonl files")
if message_count < 2:
    raise SystemExit(f"assert failed: expected >=2 /v1/messages requests, got {message_count}")

print(f"assert ok: traces={len(jsonl_files)} message_requests={message_count}")
for jf in jsonl_files:
    print(f"JSONL={jf}")
    print(f"HTML={jf.with_suffix('.html')}")
PY

echo "TRACE_DIR=$TRACE_DIR"
echo "RUN_LOG=$RUN_LOG"
