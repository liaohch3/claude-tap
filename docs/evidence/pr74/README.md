# PR 74 Evidence

PR: `fix(codex): honor env proxy settings for forward websocket upstreams`

This directory contains real evidence for the first-message `cxx` reconnect fix.
The screenshot was taken from a real interactive Codex trace after the forward
proxy started passing environment-derived proxy settings into `aiohttp.ws_connect()`.

## Real interactive run

Command:

```bash
source ~/.zshrc
cd /Users/liaohch3
cxx
```

First message entered in the session:

```text
say hello
```

Artifacts:

- JSONL: `/Users/liaohch3/.claude-tap-traces/2026-04-21/trace_162156.jsonl`
- HTML: `/Users/liaohch3/.claude-tap-traces/2026-04-21/trace_162156.html`
- Log: `/Users/liaohch3/.claude-tap-traces/2026-04-21/trace_162156.log`

Observed in log:

- `16:22:05 [Turn 15] -> WS UPGRADE /backend-api/codex/responses`
- `16:22:05 [Turn 15] -> WS upstream via proxy http://127.0.0.1:7897`
- `16:24:06 [Turn 15] <- WS closed (121520ms, 1 client->upstream, 3 upstream->client)`

Observed behavior:

- the first interactive message upgraded to websocket immediately
- no `upstream WS connect failed` entries were emitted for the session
- the session did not show the `Reconnecting...` loop that previously happened on the first message

## Screenshot

- `pr74-cxx-first-message-no-reconnect.png`
  - Source viewer: `/Users/liaohch3/.claude-tap-traces/2026-04-21/trace_162156.html`
  - Captures the real first-message websocket turn selected in the viewer

## Local validation

Commands:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest tests/ -x --timeout=60
uv run pytest tests/test_e2e.py -k "forward_proxy_connect_websocket" -x --timeout=120
uv run python scripts/check_screenshots.py docs/evidence/pr74
uv run python scripts/verify_screenshots.py /Users/liaohch3/.claude-tap-traces/2026-04-21/trace_162156.html
```

Results:

- `uv run ruff check .` -> passed
- `uv run ruff format --check .` -> passed
- `uv run pytest tests/ -x --timeout=60` -> `140 passed, 25 skipped, 4 warnings in 26.86s`
- targeted websocket regression -> `2 passed, 28 deselected`
- screenshot quality check -> `PASS=1 WARN=0 FAIL=0`
- viewer rendering verification -> `trace_162156.html: OK`
