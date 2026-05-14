# OpenCode Real Trace Evidence

Generated from real OpenCode CLI runs through `claude-tap --tap-client opencode`
on 2026-05-13.

## Real Trace Inputs

- First turn trace: `.traces/opencode-real-quality/2026-05-13/trace_161051.jsonl`
- First turn viewer: `.traces/opencode-real-quality/2026-05-13/trace_161051.html`
- Resume trace: `.traces/opencode-real-quality/2026-05-13/trace_161110.jsonl`
- Resume viewer: `.traces/opencode-real-quality/2026-05-13/trace_161110.html`
- OpenAI OAuth trace: `.traces/opencode-openai-oauth/2026-05-13/trace_164543.jsonl`
- OpenAI OAuth viewer: `.traces/opencode-openai-oauth/2026-05-13/trace_164543.html`

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

OpenAI OAuth provider:

```bash
opencode providers login -p openai
opencode providers list
opencode models openai

claude-tap --tap-client opencode \
  --tap-output-dir .traces/opencode-openai-oauth \
  --tap-no-open --tap-no-update-check -- \
  run -m openai/gpt-5.4-mini-fast \
  --format json --dangerously-skip-permissions \
  "Use the bash tool twice. First run exactly: printf 'OPENCODE_OPENAI_OAUTH_TOOL_ONE\n'; pwd . Second run exactly: printf 'OPENCODE_OPENAI_OAUTH_TOOL_TWO\n'; ls pyproject.toml . Then answer with both exact command outputs."
```

## Assertions Before Screenshots

The screenshot script opened each real HTML viewer in Chromium and asserted:

- `Full JSON` is present, but not the only section.
- `Tools`, `System Prompt`, `Messages`, and `Response` sections render.
- The OpenCode system prompt contains `You are opencode` or `You are OpenCode`.
- Tool list includes `bash`.
- Message history contains `OPENCODE_TOOL_ONE` and `OPENCODE_TOOL_TWO`.
- Tool results contain the real `pwd` output and `pyproject.toml`.
- Resume response contains `Path from OPENCODE_TOOL_ONE`.
- Token display includes `Cache Read`.
- The OpenAI OAuth trace renders `Messages` rather than `Request Context` for HTTP Responses API calls.
- The OpenAI OAuth trace captures ChatGPT Codex upstream calls at `/backend-api/codex/responses`.
- The OpenAI OAuth trace contains two real `bash` function calls, two tool outputs, and final assistant output.

## Evidence Images

- `opencode-first-01-system-tools.png`
- `opencode-first-02-tool-result-output.png`
- `opencode-resume-01-multiturn-history.png`
- `opencode-resume-02-final-output-tokens.png`
- `opencode-openai-oauth-01-system-tools-cache.png`
- `opencode-openai-oauth-02-message-history-tools.png`
- `opencode-openai-oauth-03-final-output-sse.png`
