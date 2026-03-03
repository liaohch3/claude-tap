# Agent-Agnostic Watchdog Loop (MVP)

This guide documents a real watchdog loop for `codex` and `claude` tmux sessions with incremental progress detection, explicit agent events, and delivery guardrails.

## Flow

1. Watchdog task state is initialized.
2. Agent session is started in tmux.
3. `tick` reads tmux pane tail + state file.
4. Watchdog updates state and takes action:
   - `RUNNING_GOOD`: no-op
   - `RUNNING_BAD`: sends corrective prompt only for newly appeared bad signals
   - `DONE_FAIL`: sends retry prompt
   - `STUCK`: restarts session and sends resume prompt

State is persisted at `memory/agent-tasks/<task_id>.json`.

## Why Black-Box Loops Fail

Black-box loops often fail in production for four reasons:

1. Polling-only control is delayed and brittle.
2. Historical errors in pane tails are re-detected every tick, causing corrective spam.
3. Agent progress is implicit, so watchdogs infer too much from heuristics.
4. Monitor delivery can silently misroute updates when cron targets are wrong.

This design addresses those gaps with:

1. Incremental pane tracking (`last_seen_tail_hash` + `last_seen_line_count_marker` + tail snapshot) so only newly appeared bad patterns are actionable.
2. Explicit event markers (`[WD_PROGRESS]`, `[WD_BLOCKER]`, `[WD_DONE]`) that are prioritized over regex heuristics.
3. Delivery guardrail command (`check-delivery`) that validates `openclaw cron list` has required `--channel` and `--to`.

## Files

- `scripts/agent_runner.py`: tmux wrapper (`start`, `status`, `send`, `stop`)
- `scripts/agent_watchdog.py`: state machine + decision/action executor + delivery validation
- `scripts/agent_task_ctl.sh`: controller (`help/init/start/tick/status/approve/reject/stop/check-delivery`)
- `prompts/agent-templates/`: watchdog prompt templates

## Required Agent Event Markers

Agents must emit these exact markers in normal operation:

- `[WD_PROGRESS] <message>`: meaningful forward progress.
- `[WD_BLOCKER] <message>`: explicit blocker with next unblock action.
- `[WD_DONE] <message>`: iteration done and waiting for human verdict.

Watchdog prioritizes these markers over regex heuristics in each tick.

## Quick Start (Codex)

```bash
scripts/agent_task_ctl.sh init \
  --task-id mvp-codex-1 \
  --agent codex \
  --workdir /private/tmp/claude-tap-watchdog-mvp-20260303 \
  --session-name watchdog_codex_mvp \
  --prompt-file prompts/agent-templates/corrective.md

scripts/agent_task_ctl.sh start --task-id mvp-codex-1
scripts/agent_task_ctl.sh tick --task-id mvp-codex-1 --once
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
scripts/agent_task_ctl.sh tick --task-id mvp-claude-1 --once
scripts/agent_task_ctl.sh status --task-id mvp-claude-1
```

## Human Verdict Loop

```bash
scripts/agent_task_ctl.sh reject --task-id mvp-codex-1
scripts/agent_task_ctl.sh tick --task-id mvp-codex-1 --once

scripts/agent_task_ctl.sh approve --task-id mvp-codex-1
scripts/agent_task_ctl.sh tick --task-id mvp-codex-1 --once
```

## Tight Controller UX

Show command help:

```bash
scripts/agent_task_ctl.sh help
```

One-shot tick invocation (manual or cron wrapper):

```bash
scripts/agent_task_ctl.sh tick --task-id mvp-codex-1 --once
```

## Guardrail: Monitor Delivery Validation

Validate monitor target config before/after cron updates:

```bash
scripts/agent_task_ctl.sh check-delivery \
  --task-id mvp-codex-1 \
  --channel slack \
  --to '#agent-watchdog'
```

Expected result: JSON with `"ok": true`.

## Cron and openclaw Examples

Recommended cron tick (every 3 minutes):

```cron
*/3 * * * * cd /private/tmp/claude-tap-watchdog-mvp-20260303 && scripts/agent_task_ctl.sh tick --task-id mvp-codex-1 --once >> /tmp/agent-watchdog.log 2>&1
```

Recommended openclaw cron add/edit patterns with explicit delivery:

```bash
openclaw cron add \
  --name watchdog-mvp-codex-1 \
  --schedule "*/3 * * * *" \
  --cmd "cd /private/tmp/claude-tap-watchdog-mvp-20260303 && scripts/agent_task_ctl.sh tick --task-id mvp-codex-1 --once" \
  --channel slack \
  --to '#agent-watchdog'

openclaw cron edit \
  --name watchdog-mvp-codex-1 \
  --channel slack \
  --to '#agent-watchdog'
```

Use one cron entry per task ID.

## Standard Operating Procedure

Opinionated loop for black-box continuous execution:

1. `init`: initialize state and config.
2. `start`: start agent tmux session.
3. `cron tick`: run `tick --once` every few minutes.
4. `human verdict`: `approve` or `reject`.
5. If `reject`, watchdog auto-sends retry prompt on next tick.
6. Repeat until state reaches `DONE_PASS`.

## Notes

- Incremental progress data is tracked in state with `last_seen_tail_hash` and `last_seen_line_count_marker`.
- Deterministic heuristics remain configurable through state `config` (`timeout_minutes`, `stuck_minutes`, `bad_patterns`, `action_cooldown_minutes`).
- `agent_runner.py` supports deterministic tmux session names when `--session-name` is omitted.
- Set `AGENT_RUNNER_CODEX_CMD` or `AGENT_RUNNER_CLAUDE_CMD` to override agent launch commands if needed.
