# OpenCode Real Trace Evidence

Generated from real OpenCode CLI runs through `claude-tap --tap-client opencode`
on 2026-05-13.

## Real Trace Inputs

- First turn trace: `.traces/opencode-real-quality/2026-05-13/trace_161051.jsonl`
- First turn viewer: `.traces/opencode-real-quality/2026-05-13/trace_161051.html`
- Resume trace: `.traces/opencode-real-quality/2026-05-13/trace_161110.jsonl`
- Resume viewer: `.traces/opencode-real-quality/2026-05-13/trace_161110.html`

## Runs

First turn:

```bash
claude-tap --tap-client opencode \
  --tap-output-dir .traces/opencode-real-quality \
  --tap-no-open --tap-no-update-check -- \
  run -m opencode/deepseek-v4-flash-free \
  --format json --dangerously-skip-permissions \
  "Use the shell/bash tool to run exactly: printf 'OPENCODE_TOOL_ONE\n'; pwd . Then answer with the exact output."
```

Resume turn:

```bash
claude-tap --tap-client opencode \
  --tap-output-dir .traces/opencode-real-quality \
  --tap-no-open --tap-no-update-check -- \
  run -m opencode/deepseek-v4-flash-free \
  --format json --dangerously-skip-permissions \
  --session ses_1dde5052fffefXsEYsepPZ0b09 \
  "Second turn: use the shell/bash tool to run exactly: printf 'OPENCODE_TOOL_TWO\n'; ls pyproject.toml . Then answer with both the previous OPENCODE_TOOL_ONE output path and the new command output."
```

## Assertions Before Screenshots

The screenshot script opened each real HTML viewer in Chromium and asserted:

- `Full JSON` is present, but not the only section.
- `Tools`, `System Prompt`, `Messages`, and `Response` sections render.
- The OpenCode system prompt contains `You are opencode`.
- Tool list includes `bash`.
- Message history contains `OPENCODE_TOOL_ONE` and `OPENCODE_TOOL_TWO`.
- Tool results contain the real `pwd` output and `pyproject.toml`.
- Resume response contains `Path from OPENCODE_TOOL_ONE`.
- Token display includes `Cache Read`.

## Evidence Images

- `opencode-first-01-system-tools.png`
- `opencode-first-02-tool-result-output.png`
- `opencode-resume-01-multiturn-history.png`
- `opencode-resume-02-final-output-tokens.png`
