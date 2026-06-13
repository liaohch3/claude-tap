# PR #276 double-serialized request body evidence

`double-serialized-viewer.png` was captured from a real `python -m claude_tap`
reverse-proxy run against a local fake Claude Code binary and a local fake
upstream server.

The fake Claude binary sent a double-serialized request body:

```python
inner = {"model": "claude-test", "messages": [{"role": "user", "content": "hi"}]}
payload = json.dumps(json.dumps(inner)).encode()
```

The run confirmed both required behaviors:

- The fake upstream still received the original one-pass parsed JSON string, so
  claude-tap did not mutate the request bytes sent upstream.
- The stored trace request body was a dict, allowing the viewer to render the
  user message normally instead of showing an opaque escaped JSON string.

Validation commands:

```bash
uv run ruff check claude_tap/forward_proxy.py claude_tap/proxy.py tests/test_e2e.py tests/test_request_body_parsing.py
uv run ruff format --check claude_tap/forward_proxy.py claude_tap/proxy.py tests/test_e2e.py tests/test_request_body_parsing.py
uv run pytest tests/test_request_body_parsing.py tests/test_e2e.py::test_double_serialized_request_body -q
python scripts/check_screenshots.py .agents/evidence/pr/276-double-serialized-body
```

The temporary SQLite database and exported HTML used to capture the screenshot
were local-only and are not committed. No API keys, auth tokens, private
prompts, raw trace files, or generated HTML viewers are present in this
evidence directory.
