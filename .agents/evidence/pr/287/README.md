# PR 287 Kimi Code Deep E2E Evidence

Real validation for `--tap-client kimi-code` using the latest `@moonshot-ai/kimi-code` package and a logged-in local Kimi Code profile.

## Scenario

The test used one real Kimi Code conversation resumed across three non-interactive turns. Each turn required file or shell tool use inside an isolated workspace:

- workspace: `/tmp/claude-tap-kimi-code-deep-287-workspace`
- trace DB: `/tmp/claude-tap-kimi-code-deep-287.sqlite3`
- Kimi resume id: `276aae75-89fb-4093-a6d7-5861c1acc4ef`

The workspace started with `seed.txt`. Kimi Code then created and modified:

- `turn1_notes.md`
- `turn2_report.json`
- `turn3_verification.txt`

## Commands

Turn 1:

```bash
CLOUDTAP_DB=/tmp/claude-tap-kimi-code-deep-287.sqlite3 \
npm exec --yes --package @moonshot-ai/kimi-code@latest -- bash -lc \
'/tmp/claude-tap-pr287/.venv/bin/claude-tap --tap-client kimi-code --tap-no-open --tap-no-live --tap-no-update-check -- --print --final-message-only -p "Work only inside the current directory. Use your file or shell tools, not just reasoning. First inspect the current directory. Then create turn1_notes.md with two bullet lines, one containing KIMI_DEEP_TURN1. Then run a command that reports the file line count and sha256. Then read the file back. Reply with exactly one line starting with KIMI_DEEP_TURN1_OK and include line_count and sha256."'
```

Turn 2:

```bash
CLOUDTAP_DB=/tmp/claude-tap-kimi-code-deep-287.sqlite3 \
npm exec --yes --package @moonshot-ai/kimi-code@latest -- bash -lc \
'/tmp/claude-tap-pr287/.venv/bin/claude-tap --tap-client kimi-code --tap-no-open --tap-no-live --tap-no-update-check -- -r 276aae75-89fb-4093-a6d7-5861c1acc4ef --print --final-message-only -p "Continue the existing session. Use your file or shell tools again. Inspect turn1_notes.md, append a third bullet line containing KIMI_DEEP_TURN2, create turn2_report.json containing the final line count and sha256, then read that JSON file back. Reply with exactly one line starting with KIMI_DEEP_TURN2_OK and include line_count and sha256."'
```

Turn 3:

```bash
CLOUDTAP_DB=/tmp/claude-tap-kimi-code-deep-287.sqlite3 \
npm exec --yes --package @moonshot-ai/kimi-code@latest -- bash -lc \
'/tmp/claude-tap-pr287/.venv/bin/claude-tap --tap-client kimi-code --tap-no-open --tap-no-live --tap-no-update-check -- -r 276aae75-89fb-4093-a6d7-5861c1acc4ef --print --final-message-only -p "Continue the same session for a third turn. Use file or shell tools again. Read turn2_report.json, list the workspace files, run a Python command that verifies the sha256 of turn1_notes.md matches the report, and write turn3_verification.txt containing KIMI_DEEP_TURN3 plus the verification result. Then read turn3_verification.txt back. Reply with exactly one line starting with KIMI_DEEP_TURN3_OK and include verified=true."'
```

## Results

| Turn | claude-tap session | API calls | Status | Final response |
| --- | --- | ---: | --- | --- |
| 1 | `1ee9799d-d48f-404b-99c4-f7182fc825dc` | 5 | `complete` | `KIMI_DEEP_TURN1_OK line_count=2 sha256=0473e9a2b20bd4f62ad857f28ae8e4323859f23cfc9cffd6bc6d345b0ea99fd9` |
| 2 | `412db154-5fe6-4b91-9093-3f51f83f7e4c` | 6 | `complete` | `KIMI_DEEP_TURN2_OK line_count=3 sha256=c027e93c45bfbb4194e08d6f5d523949ff8bdf267a77a4f05d66bbf4b3b59367` |
| 3 | `0380294c-9875-4316-9564-809d94a7a4a9` | 5 | `complete` | `KIMI_DEEP_TURN3_OK verified=true` |

Workspace verification:

```text
turn1_notes.md:
- This is turn1_notes.md
- KIMI_DEEP_TURN1
- KIMI_DEEP_TURN2

turn2_report.json:
{"line_count":3,"sha256":"c027e93c45bfbb4194e08d6f5d523949ff8bdf267a77a4f05d66bbf4b3b59367"}

turn3_verification.txt:
KIMI_DEEP_TURN3 verified=True
```

## Interactive tmux E2E

I also ran Kimi Code as a real interactive TUI process under tmux and sent three consecutive prompts with `tmux paste-buffer` / `tmux send-keys`. This validates a single running Kimi Code process, terminal input handling, tool approval prompts, and continuous conversation state without relying on `--print` or `-r` for each turn.

- tmux session: `ctap-kimi-287`
- workspace: `/tmp/claude-tap-kimi-code-tmux-287-workspace`
- trace DB: `/tmp/claude-tap-kimi-code-tmux-287.sqlite3`
- claude-tap session: `b00ecd17-4c6c-40a6-829a-6f5fdbfc5719`
- Kimi Code session: `81668bcd-c11e-455f-ab55-280dc4da9031`

Interactive prompts sent:

1. Create and read back `tmux_turn1.txt`, then reply `KIMI_TMUX_TURN1_OK`.
2. Continue in the same TUI session, append `KIMI_TMUX_TURN2`, run `wc -l` and `sha256sum`, then reply `KIMI_TMUX_TURN2_OK`.
3. Continue in the same TUI session, list files, run Python verification, write/read `tmux_turn3_check.txt`, then reply `KIMI_TMUX_TURN3_OK`.

The trace captured:

- 12 total API records
- `client=kimi-code`, `proxy_mode=reverse`, `status=complete`
- final response: `KIMI_TMUX_TURN3_OK`
- tool evidence for `Shell`, `WriteFile`, and `ReadFile`
- real tool approval flow in the interactive TUI

Workspace verification:

```text
tmux_turn1.txt:
KIMI_TMUX_TURN1 KIMI_TMUX_287
KIMI_TMUX_TURN2

tmux_turn3_check.txt:
KIMI_TMUX_TURN3 verified=true
```

## Screenshots

All screenshots were taken from real exported viewer HTML generated from the SQLite trace DB. The scroll screenshots intentionally capture different detail-pane positions rather than only the first viewport.

- `kimi-code-deep-viewer-turn3-overview.png` - third-turn viewer overview with Kimi Code sidebar entries and tool labels.
- `kimi-code-deep-viewer-turn3-mid-scroll.png` - scrolled detail pane showing conversation history and tool-call context.
- `kimi-code-deep-viewer-turn3-tool-scroll.png` - deeper scroll showing Shell and ReadFile tool results plus resumed user input.
- `kimi-code-deep-viewer-turn3-final-response-scroll.png` - scrolled third-turn tool sequence before the final verification.
- `kimi-code-deep-viewer-turn3-bottom-final-response.png` - bottom scroll showing `turn3_verification.txt` readback and `KIMI_DEEP_TURN3_OK verified=true`.
- `kimi-code-deep-viewer-turn2-overview.png` - second-turn viewer overview.
- `kimi-code-deep-viewer-turn2-tool-scroll.png` - second-turn scrolled tool call/result evidence.
- `kimi-code-tmux-viewer-overview.png` - interactive tmux run overview with 12 records and tool labels.
- `kimi-code-tmux-viewer-turn1-tools-scroll.png` - first tmux prompt scrolled tool evidence.
- `kimi-code-tmux-viewer-turn2-history-scroll.png` - second tmux prompt scrolled history/tool evidence.
- `kimi-code-tmux-viewer-turn3-tools-scroll.png` - third tmux prompt scrolled tool evidence.
- `kimi-code-tmux-viewer-bottom-final-response.png` - bottom scroll showing `KIMI_TMUX_TURN3_OK`.

## Validation

```bash
uv run python scripts/check_screenshots.py .agents/evidence/pr/287
uv run python scripts/verify_screenshots.py /tmp/claude-tap-kimi-code-deep-287-turn2.html /tmp/claude-tap-kimi-code-deep-287-turn3.html
uv run python scripts/verify_screenshots.py /tmp/claude-tap-kimi-code-tmux-287.html
```

Results:

- screenshot quality: `PASS=12 WARN=0 FAIL=0`
- viewer HTML render verification: all exported HTML files passed
