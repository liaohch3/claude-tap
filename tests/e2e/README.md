# Real E2E Tests

These tests exercise claude-tap with the **real Claude CLI** — no mocks, no fakes.
They start claude-tap from local source code, connect to an actual `claude` binary,
send real prompts, and verify the resulting trace output.

## Prerequisites

1. **Claude CLI installed and authenticated:**
   ```bash
   claude --version
   claude -p "hello"   # Should work without errors
   ```

2. **claude-tap installed from local source:**
   The test fixtures handle this automatically via `pip install -e .`

3. **Python dependencies:**
   ```bash
   uv sync --extra dev
   ```

## Running

```bash
# Run all real E2E tests
uv run pytest tests/e2e/ --run-real-e2e --timeout=300

# Run a specific test
uv run pytest tests/e2e/test_real_proxy.py::TestRealProxy::test_single_turn --run-real-e2e --timeout=180

# Run with verbose output
uv run pytest tests/e2e/ --run-real-e2e --timeout=300 -v -s
```

## Skipping in CI

These tests are **skipped by default** in CI and local runs. They only execute when
the `--run-real-e2e` flag is explicitly passed. This is controlled by the
`pytest_collection_modifyitems` hook in `conftest.py`.

## Test Cases

| Test | What It Verifies |
|------|-----------------|
| `test_single_turn` | Basic prompt-response captured in trace |
| `test_multi_turn` | Conversation memory works with `-c` flag |
| `test_tool_use` | Tool use generates multiple trace records |
| `test_html_viewer_generated` | JSONL trace files are properly created |
| `test_api_key_redaction` | No raw API keys leak into trace files |
| `test_streaming_sse_capture` | SSE events captured in streaming responses |

## Troubleshooting

- **Tests time out:** Increase `--timeout` or check network connectivity
- **Claude CLI not found:** Ensure `claude` is in PATH
- **Authentication errors:** Run `claude` interactively first to authenticate
- **Port conflicts:** Tests use auto-assigned ports (port 0), conflicts are unlikely

## Architecture

```
conftest.py
  ├── pytest_addoption      # Adds --run-real-e2e flag
  ├── pytest_collection_modifyitems  # Skips tests when flag not set
  ├── installed_claude_tap   # pip install -e from local source
  ├── proxy_server           # Starts claude-tap --tap-no-launch
  └── claude_env             # Sets ANTHROPIC_BASE_URL to proxy

test_real_proxy.py
  ├── _wait_for_trace_files  # Polls trace dir for JSONL records
  ├── _run_claude_prompt     # Runs `claude -p <prompt>`
  └── TestRealProxy          # All test cases
```
