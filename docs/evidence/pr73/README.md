# PR 73 Evidence

PR: `fix(codex): relay websocket traffic in forward proxy`

This directory contains real evidence for PR #73. The screenshots were taken from the generated trace viewer HTML files of two real Codex runs after the forward-proxy websocket relay fix.

## Real runs

### 1. Direct forward-mode Codex run

Command:

```bash
cd /Users/liaohch3
uv run --project /Users/liaohch3/src/github.com/liaohch3/claude-tap \
  python -m claude_tap \
  --tap-client codex \
  --tap-proxy-mode forward \
  --tap-output-dir /tmp/codex-forward-real \
  --tap-no-open \
  --tap-no-update-check \
  -- exec "run pwd and reply with exactly the pwd" \
  --dangerously-bypass-approvals-and-sandbox
```

Artifacts:

- JSONL: `/tmp/codex-forward-real/2026-04-20/trace_011250.jsonl`
- HTML: `/tmp/codex-forward-real/2026-04-20/trace_011250.html`
- Log: `/tmp/codex-forward-real/2026-04-20/trace_011250.log`

Observed in log:

- `WS UPGRADE /backend-api/codex/responses`
- `WS closed (18329ms, 3 client->upstream, 56 upstream->client)`

Observed in trace:

- `transport=websocket`

### 2. Real alias run via `cxx`

Command:

```bash
source ~/.zshrc
cd /Users/liaohch3
cxx exec "run pwd and reply with exactly the pwd"
```

Artifacts:

- JSONL: `/Users/liaohch3/.claude-tap-traces/2026-04-20/trace_011853.jsonl`
- HTML: `/Users/liaohch3/.claude-tap-traces/2026-04-20/trace_011853.html`
- Log: `/Users/liaohch3/.claude-tap-traces/2026-04-20/trace_011853.log`

Observed in log:

- `WS UPGRADE /backend-api/codex/responses`
- `WS closed (10023ms, 3 client->upstream, 70 upstream->client)`

Observed behavior:

- caller working directory remained `/Users/liaohch3`
- no `405 Method Not Allowed` websocket fallback noise
- no `SSL connection is closed` terminal spam

### 3. Real alias rerun after websocket output reconstruction fix

Command:

```bash
source ~/.zshrc
cd /Users/liaohch3
cxx exec "run pwd and reply with exactly the pwd"
```

Artifacts:

- JSONL: `/Users/liaohch3/.claude-tap-traces/2026-04-20/trace_104528.jsonl`
- HTML: `/Users/liaohch3/.claude-tap-traces/2026-04-20/trace_104528.html`
- Log: `/Users/liaohch3/.claude-tap-traces/2026-04-20/trace_104528.log`

Observed in log:

- `WS UPGRADE /backend-api/codex/responses`
- `WS closed (10186ms, 3 client->upstream, 73 upstream->client)`

Observed in trace:

- `response.body.output[0].content[0].text == "/Users/liaohch3"`

Observed behavior:

- top-level websocket response body contains the final assistant message
- viewer now renders the final answer instead of showing only the request payload

## Screenshots

- `pr73-direct-forward-websocket-fixed-output.png`
  - Source viewer: `/tmp/codex-forward-real-fixed/2026-04-20/trace_112817.html`
  - Captures the repaired direct forward-mode run with websocket upgrade and visible final assistant output
- `pr73-cxx-forward-websocket-fixed-output.png`
  - Source viewer: `/Users/liaohch3/.claude-tap-traces/2026-04-20/trace_104528.html`
  - Captures the repaired alias run with visible final assistant output

## Local validation

Commands:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest tests/ -x --timeout=60
uv run pytest tests/test_e2e.py -k "forward_proxy_connect_websocket or forward_proxy_connect" -x --timeout=120
uv run pytest tests/test_ws_proxy.py -k build_ws_record_merges_incremental_request_and_output_items -x --timeout=120
uv run pytest tests/test_responses_browser.py -x --timeout=120
uv run python scripts/check_screenshots.py docs/evidence/pr73
uv run python scripts/verify_screenshots.py \
  /Users/liaohch3/.claude-tap-traces/2026-04-20/trace_011853.html \
  /tmp/codex-forward-real/2026-04-20/trace_011250.html
uv run python scripts/verify_screenshots.py /Users/liaohch3/.claude-tap-traces/2026-04-20/trace_104528.html
```

Results:

- `uv run ruff check .` -> passed
- `uv run ruff format --check .` -> passed
- `uv run pytest tests/ -x --timeout=60` -> `138 passed, 25 skipped, 4 warnings in 26.63s`
- targeted websocket regression -> `2 passed, 27 deselected in 0.82s`
- websocket reconstruction unit test -> `1 passed, 9 deselected in 0.19s`
- browser reconstruction regression -> `3 passed in 1.48s`
- screenshot quality check -> `PASS=4 WARN=0 FAIL=0`
- viewer rendering verification -> all 3 viewer HTML files passed
