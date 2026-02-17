---
name: e2e-test
description: Run claude-tap end-to-end tests using tmux to drive real Claude Code sessions
user_invocable: true
---

# claude-tap E2E Test

Run this skill after modifying core logic in claude-tap, especially:
- Proxy handler / SSE reassembly (`__init__.py`)
- TraceWriter (JSONL writing, flush behavior)
- HTML viewer generation (`viewer.html`, `_generate_html_viewer`)
- LiveViewerServer (SSE streaming)
- Signal handling / graceful shutdown

## Steps

1. Run the E2E test script:

```bash
./test_e2e_tap.sh
```

Or run a single scenario:

```bash
./test_e2e_tap.sh --test normal   # Normal mode + /exit
./test_e2e_tap.sh --test live     # --tap-live mode + /exit
./test_e2e_tap.sh --test ctrlc   # Normal mode + Ctrl+C
```

2. Read the output. Each check prints `PASS` or `FAIL`. The final summary shows total pass/fail counts.

3. If tests fail, check:
   - **`.jsonl created on startup` fails**: TraceWriter file creation issue. Check `TraceWriter.__init__` and `async_main` for path handling.
   - **`.jsonl has content after interaction` fails**: Proxy not intercepting requests, or TraceWriter not flushing. Check `proxy_handler`, `_handle_streaming`, and `TraceWriter.write`.
   - **`.html generated after exit` fails**: `_generate_html_viewer` not called or erroring during shutdown. Check the `finally` block in `async_main`.
   - **`.jsonl content is valid JSON` fails**: Malformed JSON being written. Check `TraceWriter.write` serialization.
   - **Timeout warnings**: Claude Code taking too long to start or respond. May be a network/API issue, not a claude-tap bug.

4. Test artifacts are saved in `/tmp/claude-tap-e2e-*` for manual inspection.
