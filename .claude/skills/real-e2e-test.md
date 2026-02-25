---
description: Run real E2E tests that connect to actual Claude CLI
tags: testing, e2e, integration
---

# Real E2E Test Skill

Run real end-to-end tests that start claude-tap from local source, connect to the
real Claude CLI, and verify trace output.

## Prerequisites

- `claude` CLI installed and authenticated
- Python dev dependencies installed: `uv sync --extra dev`

## Commands

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

## Notes

- These tests are **skipped by default** — the `--run-real-e2e` flag is required
- Each test starts a fresh proxy server and trace directory (via fixtures)
- Tests use auto-assigned ports to avoid conflicts
- Timeouts are generous (180-300s) because real Claude API calls are involved
- Trace directories are cleaned up automatically after each test

## Test Cases

| Test | Timeout | What It Tests |
|------|---------|---------------|
| `test_single_turn` | 180s | Basic prompt/response trace capture |
| `test_multi_turn` | 300s | Conversation memory with `-c` flag |
| `test_tool_use` | 180s | Tool use generates multiple trace records |
| `test_html_viewer_generated` | 180s | JSONL trace files created correctly |
| `test_api_key_redaction` | 180s | API keys redacted from trace output |
| `test_streaming_sse_capture` | 180s | SSE events captured in streaming mode |
