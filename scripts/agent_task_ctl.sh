#!/usr/bin/env bash
set -euo pipefail

# Thin controller for agent task lifecycle.
# Usage:
#   scripts/agent_task_ctl.sh init --task-id t1 --agent codex --workdir /path --prompt-file prompts/start.md
#   scripts/agent_task_ctl.sh start --task-id t1
#   scripts/agent_task_ctl.sh tick --task-id t1
#   scripts/agent_task_ctl.sh status --task-id t1
#   scripts/agent_task_ctl.sh approve --task-id t1
#   scripts/agent_task_ctl.sh reject --task-id t1
#   scripts/agent_task_ctl.sh stop --task-id t1

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <init|start|tick|status|approve|reject|stop> [args...]" >&2
  exit 1
fi

CMD="$1"
shift

case "$CMD" in
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
  *)
    echo "unknown command: $CMD" >&2
    exit 1
    ;;
esac
