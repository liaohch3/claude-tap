---
name: real-e2e-test
description: Run real E2E tests against Claude CLI in pytest and tmux modes
tags: testing, e2e, integration, tmux
---

# Real E2E Test Skill

Run real end-to-end tests that start `claude-tap` from local source, connect to the
real Claude CLI, and verify trace output.

## Prerequisites

- `claude` CLI installed and authenticated
- Python dev dependencies installed: `uv sync --extra dev`
- `tmux` installed for interactive mode (`brew install tmux`)

## Mode 1: Pytest Real E2E (7 test cases)

### Run all real E2E tests
```bash
uv run pytest tests/e2e/ --run-real-e2e --timeout=300 -v
```

### Run a single test
```bash
uv run pytest tests/e2e/test_real_proxy.py::TestRealProxy::test_single_turn --run-real-e2e --timeout=180 -v -s
```

### Run with debug output
```bash
uv run pytest tests/e2e/ --run-real-e2e --timeout=300 -v -s --tb=long
```

## Mode 2: tmux Interactive Real E2E

Use this when you need to validate non-`-p` interactive behavior in Claude Code TUI.

```bash
scripts/run_real_e2e_tmux.sh
```

Optional overrides:

```bash
PROMPT_ONE="Use the shell tool to run command ls in the current directory, then reply with any 5 filenames only." \
PROMPT_TWO="Thank you." \
SUBMIT_KEY="Enter" \
PERMISSION_MODE="bypassPermissions" \
scripts/run_real_e2e_tmux.sh
```

Important tmux interaction notes:

- Submit key is `Enter` for Claude Code TUI in tmux (confirmed working).
- `PROMPT_ONE` should intentionally trigger tool use.
- For portability, use `grep -F` instead of `rg` in shell assertions (`rg` may be unavailable).

## Verification Checklist (for both modes)

- Latest trace `.jsonl` contains both prompts (`PROMPT_ONE`, `PROMPT_TWO`)
- At least 2 requests hit `/v1/messages`
- At least one response content block has `"type": "tool_use"`
- HTML viewer file is generated (`trace_*.html`)

## Notes

- Real E2E tests are skipped by default; `--run-real-e2e` is required.
- Each pytest case starts a fresh proxy server and trace directory.
- Timeouts are intentionally generous because real API calls are involved.
- tmux mode includes retry logic for prompt submission and post-run JSONL assertions.

## Pytest Test Cases

| Test | Timeout | What It Tests |
|------|---------|---------------|
| `test_single_turn` | 180s | Basic prompt/response trace capture |
| `test_multi_turn` | 300s | Conversation memory with `-c` flag |
| `test_tool_use` | 180s | Tool use generates multiple trace records |
| `test_html_viewer_generated` | 180s | HTML viewer generated with embedded trace data |
| `test_api_key_redaction` | 180s | API keys redacted from trace output |
| `test_streaming_sse_capture` | 180s | SSE events captured in streaming mode |
| `test_trace_summary` | 180s | CLI stdout includes trace summary and API call count |
