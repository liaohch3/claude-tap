---
name: e2e-test
description: Run claude-tap end-to-end tests with pytest
user_invocable: true
---

# claude-tap E2E Test

Run this skill after modifying core logic in claude-tap, especially:
- Proxy handler / SSE reassembly (`__init__.py`)
- TraceWriter (JSONL writing, flush behavior)
- HTML viewer generation (`viewer.html`, `_generate_html_viewer`)
- LiveViewerServer (SSE streaming)
- Signal handling / graceful shutdown
- Smart update check / trace cleanup

## Steps

1. Run the full test suite:

```bash
uv run pytest tests/test_e2e.py -v --timeout=120
```

Or run a single test:

```bash
uv run pytest tests/test_e2e.py::test_e2e -v           # Full E2E pipeline
uv run pytest tests/test_e2e.py::test_trace_cleanup -v  # Trace cleanup
uv run pytest tests/test_e2e.py::test_version_check_with_fake_pypi -v  # Update check
```

2. Read the output. Each test prints `PASSED` or `FAILED`.

3. If tests fail, check:
   - **test_e2e fails**: Core proxy pipeline issue. Check `proxy_handler`, `_handle_streaming`, `TraceWriter.write`.
   - **test_trace_cleanup fails**: Manifest logic issue. Check `_load_manifest`, `_cleanup_traces`, `_register_trace`.
   - **test_version_check_* fails**: PyPI check logic. Check `_check_pypi_version`, `CLAUDE_TAP_PYPI_URL` env var.
   - **test_live_viewer_* fails**: Viewer HTML issues. Check `viewer.html` for `preserveDetail` chain, `updateNavButtons`.
   - **Timeout**: May be a network/port issue, not a claude-tap bug.
