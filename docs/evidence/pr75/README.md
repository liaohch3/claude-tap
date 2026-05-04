# PR 75 Evidence

PR: `fix(viewer): label Codex responses input as request context`

This directory contains real evidence for the Codex responses viewer fix.
The screenshot was taken from a real archived trace after the viewer stopped
labeling Codex `request.body.input` as normal `Messages`.

## Real sample

Artifacts:

- JSONL: `/Users/liaohch3/Desktop/claude-tap-jsonl-samples/trace_100137.jsonl`
- HTML: `/Users/liaohch3/Desktop/claude-tap-jsonl-samples/trace_100137.html`

Selected entry:

- `Turn 20`
- `WEBSOCKET /backend-api/codex/responses`

Observed before the fix:

- the detail pane showed `Messages`
- that section included many historical `assistant` messages from `request.body.input`
- this looked like the current turn emitted multiple consecutive assistant replies

Observed after the fix:

- the same section is labeled `Request Context`
- the actual current-turn output remains under `Response`
- the UI no longer implies that historical assistant context is the current response

## Screenshot

- `pr75-codex-request-context-label.png`
  - Source viewer: `/Users/liaohch3/Desktop/claude-tap-jsonl-samples/trace_100137.html`
  - Captures the selected `Turn 20` entry after the viewer fix

## Local validation

Commands:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest tests/ -x --timeout=60
uv run python scripts/check_screenshots.py docs/evidence/pr75
uv run python scripts/verify_screenshots.py /Users/liaohch3/Desktop/claude-tap-jsonl-samples/trace_100137.html
```

Results:

- `uv run ruff check .` -> passed
- `uv run ruff format --check .` -> passed
- `uv run pytest tests/ -x --timeout=60` -> `141 passed, 25 skipped, 4 warnings in 26.93s`
- screenshot quality check -> `PASS=1 WARN=0 FAIL=0`
- viewer rendering verification -> `trace_100137.html: OK`
