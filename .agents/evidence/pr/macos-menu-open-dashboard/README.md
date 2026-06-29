# macOS menu monitor E2E evidence

Date: 2026-06-29

Branch: `codex/macos-menu-app`

Evidence directory, local only: `.traces/macos-menu-e2e-20260629-144944`

## Environment

- macOS 26.5.1, arm64
- Built app bundle with `.venv/bin/python -m claude_tap build-macos-app`
- Launched app bundle through LaunchServices with `open -n "dist/Claude Tap.app" --args --tap-no-auto-start`
- `uv` was available at `/Users/petershi/.local/bin/uv`, but not on the default shell `PATH`
- Claude Code CLI: `/Users/petershi/.local/bin/claude`, version `2.1.191`
- Codex CLI: `/Applications/Codex.app/Contents/Resources/codex`, version `0.142.3`

## Config Baseline

- `~/.claude/settings.json`: `d654e9c53be723101fd16090abd01c130e983afa37481d69008f98c0e209e555`, 1948 bytes, mode `600`
- `~/.codex/config.toml`: `0f2c9a9e36058286cfc778fbb54f1fcd052b6a6dc6398c0dade2aa8de99afaf1`, 3984 bytes, mode `600`

## Injection

Starting the monitor created `~/.claude-tap/monitor-state.json`, backup files for both configs, and dashboard/proxy process records:

- `dashboard`
- `claude proxy`
- `codex proxy`

Injected values observed:

- Claude: `ANTHROPIC_BASE_URL=http://127.0.0.1:19528`
- Codex: `openai_base_url = "http://127.0.0.1:19529/v1"`

## Client Capture

Codex real CLI run:

- Command: `codex exec --skip-git-repo-check --sandbox read-only --output-last-message ...`
- Prompt marker: `CODEX_TAP_MACOS_E2E_OK`
- Exit status: `0`
- Dashboard DB result: `client=codex`, `proxy_mode=reverse`, `record_count=14`, `model=gpt-5.5`

Claude Code real CLI run:

- Command: `claude -p ... --output-format json --max-budget-usd 0.05 --permission-mode dontAsk`
- Exit status: `1`
- Result: `Not logged in · Please run /login`
- `ANTHROPIC_API_KEY` was not present in the shell environment
- Dashboard DB result: `client=claude`, `proxy_mode=reverse`, `record_count=0`

## Restore

After client validation, `claude-tap monitor-restore` returned status `0`.

- `~/.claude-tap/monitor-state.json`: absent
- `~/.claude/settings.json`: byte-exact baseline restore, hash `d654e9c53be723101fd16090abd01c130e983afa37481d69008f98c0e209e555`
- `~/.codex/config.toml`: byte-exact baseline restore, hash `0f2c9a9e36058286cfc778fbb54f1fcd052b6a6dc6398c0dade2aa8de99afaf1`

Normal controller stop cycle:

- `controller.stop()` returned `true`
- Monitor state removed
- Claude config restored byte-for-byte
- Codex config restored byte-for-byte

Force-kill recovery:

- Started active monitor with recorded PIDs: dashboard `82942`, Claude proxy `82943`, Codex proxy `82944`
- Killed parent process `82931` without calling stop
- Confirmed recorded dashboard/proxy children remained and monitor state was present
- Ran `claude-tap monitor-restore`
- Monitor state removed
- Recorded dashboard/proxy children were cleaned up
- Claude config restored byte-for-byte
- Codex config restored byte-for-byte

## Gaps

- Literal menu clicking through System Events was blocked by macOS TCC: `osascript is not allowed assistive access`.
- Full Claude Code request capture was blocked by local authentication state: Claude CLI reported `Not logged in · Please run /login`, and no `ANTHROPIC_API_KEY` was present.
