# Agent-Agnostic Watchdog Loop (MVP)

This guide documents a minimal, real watchdog loop that works for both `codex` and `claude` tmux sessions.

## Flow

1. WatchDog task state is initialized.
2. Agent session is started in tmux.
3. `tick` reads tmux pane tail + state file.
4. Watchdog updates state and takes action:
   - `RUNNING_GOOD`: no-op
   - `RUNNING_BAD`: sends corrective prompt
   - `DONE_FAIL`: sends retry prompt
   - `STUCK`: restarts session and sends resume prompt

State is persisted at `memory/agent-tasks/<task_id>.json`.

## Files

- `scripts/agent_runner.py`: tmux wrapper (`start`, `status`, `send`, `stop`)
- `scripts/agent_watchdog.py`: state machine + decision/action executor
- `scripts/agent_task_ctl.sh`: thin controller (`init/start/tick/status/approve/reject/stop`)
- `prompts/agent-templates/`: watchdog prompt templates

## Quick Start (Codex)

```bash
scripts/agent_task_ctl.sh init \
  --task-id mvp-codex-1 \
  --agent codex \
  --workdir /private/tmp/claude-tap-watchdog-mvp-20260303 \
  --session-name watchdog_codex_mvp \
  --prompt-file prompts/agent-templates/corrective.md

scripts/agent_task_ctl.sh start --task-id mvp-codex-1
scripts/agent_task_ctl.sh tick --task-id mvp-codex-1
scripts/agent_task_ctl.sh status --task-id mvp-codex-1
```

## Quick Start (Claude)

```bash
scripts/agent_task_ctl.sh init \
  --task-id mvp-claude-1 \
  --agent claude \
  --workdir /private/tmp/claude-tap-watchdog-mvp-20260303 \
  --session-name watchdog_claude_mvp \
  --prompt-file prompts/agent-templates/corrective.md

scripts/agent_task_ctl.sh start --task-id mvp-claude-1
scripts/agent_task_ctl.sh tick --task-id mvp-claude-1
scripts/agent_task_ctl.sh status --task-id mvp-claude-1
```

## Human Verdict Loop

```bash
scripts/agent_task_ctl.sh reject --task-id mvp-codex-1
scripts/agent_task_ctl.sh tick --task-id mvp-codex-1

scripts/agent_task_ctl.sh approve --task-id mvp-codex-1
scripts/agent_task_ctl.sh tick --task-id mvp-codex-1
```

## Cron Pattern (Every 3 Minutes)

Example crontab entry:

```cron
*/3 * * * * cd /private/tmp/claude-tap-watchdog-mvp-20260303 && scripts/agent_task_ctl.sh tick --task-id mvp-codex-1 >> /tmp/agent-watchdog.log 2>&1
```

Use one cron line per task id.

## Notes

- Deterministic heuristics are configurable through state `config` values (`timeout_minutes`, `stuck_minutes`, `bad_patterns`, `action_cooldown_minutes`).
- `agent_runner.py` supports deterministic tmux session names when `--session-name` is omitted.
- Set `AGENT_RUNNER_CODEX_CMD` or `AGENT_RUNNER_CLAUDE_CMD` to override agent launch commands if needed.
