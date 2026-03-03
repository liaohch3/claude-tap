#!/usr/bin/env bash
set -euo pipefail

# Thin controller for agent task lifecycle.
# Usage:
#   scripts/agent_task_ctl.sh init --task-id t1 --agent codex --workdir /path --prompt-file prompts/start.md
#   scripts/agent_task_ctl.sh start --task-id t1
#   scripts/agent_task_ctl.sh tick --task-id t1 [--once]
#   scripts/agent_task_ctl.sh status --task-id t1
#   scripts/agent_task_ctl.sh approve --task-id t1
#   scripts/agent_task_ctl.sh reject --task-id t1
#   scripts/agent_task_ctl.sh stop --task-id t1
#   scripts/agent_task_ctl.sh check-delivery --channel slack --to '#ops' --task-id t1
#   scripts/agent_task_ctl.sh help

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

print_help() {
  cat <<'EOF'
Usage:
  scripts/agent_task_ctl.sh <command> [args...]

Commands:
  init            Initialize watchdog task state.
  start           Start tmux agent session for task.
  tick            Run one watchdog evaluation tick (accepts --once).
  status          Print watchdog state JSON.
  approve         Record human approve verdict.
  reject          Record human reject verdict.
  stop            Stop tmux agent session for task.
  check-delivery  Verify openclaw cron output contains required --channel and --to.
  help            Show this message.

Examples:
  scripts/agent_task_ctl.sh tick --task-id mvp-codex-1 --once
  scripts/agent_task_ctl.sh check-delivery --task-id mvp-codex-1 --channel slack --to '#agent-watchdog'
EOF
}

if [[ $# -lt 1 ]]; then
  print_help >&2
  exit 2
fi

CMD="$1"
shift

case "$CMD" in
  help|-h|--help)
    print_help
    ;;
  init)
    uv run python scripts/agent_watchdog.py init "$@"
    ;;
  start)
    uv run python scripts/agent_watchdog.py start "$@"
    ;;
  tick)
    uv run python scripts/agent_watchdog.py tick "$@"
    ;;
  status)
    uv run python scripts/agent_watchdog.py status "$@"
    ;;
  approve)
    uv run python scripts/agent_watchdog.py verdict --verdict approve "$@"
    ;;
  reject)
    uv run python scripts/agent_watchdog.py verdict --verdict reject "$@"
    ;;
  stop)
    uv run python scripts/agent_watchdog.py stop "$@"
    ;;
  check-delivery)
    uv run python scripts/agent_watchdog.py check-delivery "$@"
    ;;
  *)
    echo "unknown command: $CMD" >&2
    print_help >&2
    exit 2
    ;;
esac
