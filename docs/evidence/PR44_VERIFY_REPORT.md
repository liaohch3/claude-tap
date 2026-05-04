# PR #44 Verification Report

Date: 2026-03-19
PR: https://github.com/liaohch3/claude-tap/pull/44
Branch: `fix/codex-responses-40-41`

## Scope

This PR fixes viewer and parser support for OpenAI Codex Responses traces:
- `#40` Empty thinking block, zero token counts, null response body
- `#41` User messages missing in HTML/JSONL viewer

## Verification

### 1. Unit and integration tests

```bash
uv run pytest tests/test_responses_support.py tests/test_responses_browser.py -q  # 5 passed
uv run pytest tests/ -x --timeout=60 -q  # 103 passed, 25 skipped
uv run ruff check . && uv run ruff format --check .  # clean
```

### 2. Real Codex E2E through claude-tap (OAuth path)

Command:

```bash
claude-tap --tap-client codex \
  --tap-target https://chatgpt.com/backend-api/codex \
  --tap-output-dir /tmp/pr44-real-e2e \
  --tap-no-open --tap-no-update-check \
  -- exec "Read pyproject.toml in <repo> and tell me the project name and version" \
  --dangerously-bypass-approvals-and-sandbox
```

Result: **4 API calls captured** (2x `POST /v1/responses` + 2x `GET /v1/models`)

| Call | Turn | Endpoint | Tokens | Duration | Status |
|------|------|----------|--------|----------|--------|
| 1 | 1 | GET /v1/models | 0 | 5.3s | 200 |
| 2 | 2 | POST /v1/responses | 16,200 | 5.5s | 200 |
| 3 | 3 | GET /v1/models | 0 | 2.2s | 200 |
| 4 | 4 | POST /v1/responses | 16,343 | 2.8s | 200 |

Total: 32,543 tokens (32,377 input + 166 output)

Key observations:
- Codex OAuth falls back from WebSocket to HTTP/SSE when proxied
- Each Responses call includes 112 SSE events with full tool-use traces
- The agent performed shell commands (`sed`, `find`, `rg`) within a single session

### 3. Viewer evidence screenshots

Captured via Playwright at `1440x1000` viewport from the real HTML trace.

- `pr44-turn2-messages.png`: Sidebar shows 4 calls (2 Codex/gpt-5.4 + 2 unknown/models).
  Messages section expanded with `developer` (system prompt) and `user` message clearly visible.
  Token counts displayed (16,200 tok, 16,343 tok). **Proves #41 fix**.
- `pr44-turn2-response.png`: Response section expanded showing assistant reply with
  project name and version. Token usage and SSE events present. **Proves #40 fix**.

## What Was Verified

- Real Codex OAuth trace captured by `claude-tap` as Responses format
- Viewer displays user messages from `request.body.input` (fix for #41)
- Viewer displays assistant response text and token usage (fix for #40)
- Multiple API calls visible in sidebar (real agent behavior, not single-shot)
- SSE events preserved and browsable in viewer

## Merge Recommendation

Recommendation: **MERGE**.
