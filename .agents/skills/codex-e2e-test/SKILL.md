---
description: Run real E2E tests against Codex CLI (OpenAI Responses API) through claude-tap proxy
tags: testing, e2e, codex, responses-api
---

# Codex E2E Test Skill

Run real end-to-end tests that start `claude-tap` from local source, connect to
the real Codex CLI via OAuth, and verify Responses API trace output.

## Prerequisites

- `codex` CLI installed (`npm install -g @openai/codex`) and authenticated via OAuth
- Python dev dependencies: `uv sync --extra dev`
- Playwright installed: `uv run playwright install chromium`

Verify OAuth works:

```bash
codex exec "say hello" --dangerously-bypass-approvals-and-sandbox
```

If it fails with token errors, re-authenticate:

```bash
codex auth login
```

## Key Difference from Claude E2E

Codex uses the **OpenAI Responses API** (`/v1/responses`) instead of Anthropic Messages API.
With OAuth authentication, the upstream is `https://chatgpt.com/backend-api/codex`,
**not** `https://api.openai.com`.

The proxy must be told the correct target with `--tap-target`.

## Run a Real Codex E2E Trace

### Simple (single tool call)

```bash
claude-tap --tap-client codex \
  --tap-target https://chatgpt.com/backend-api/codex \
  --tap-output-dir /tmp/codex-e2e \
  --tap-no-open --tap-no-update-check \
  -- exec "say hello" \
  --dangerously-bypass-approvals-and-sandbox
```

### Multi-call (triggers multiple API requests)

Use a task that requires shell tool use — this forces the agent to make multiple
Responses API calls (models lookup + actual responses):

```bash
claude-tap --tap-client codex \
  --tap-target https://chatgpt.com/backend-api/codex \
  --tap-output-dir /tmp/codex-e2e \
  --tap-no-open --tap-no-update-check \
  -- exec "Read pyproject.toml and tell me the project name and version" \
  --dangerously-bypass-approvals-and-sandbox
```

Expected: 4+ API calls (2x `GET /v1/models` + 2x `POST /v1/responses`).

## Taking Viewer Screenshots with Playwright

```python
from playwright.sync_api import sync_playwright
import time, glob

html = glob.glob("/tmp/codex-e2e/trace_*.html")[-1]

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.goto(f"file://{html}")
    page.wait_for_load_state("networkidle")
    time.sleep(1)

    # Select a Responses call (data-idx matches trace line index)
    page.click('.sidebar-item[data-idx="1"]')
    time.sleep(0.5)

    # Collapse System Prompt, keep Messages open
    page.evaluate("""() => {
        const h = document.querySelectorAll('.section-header')[1];
        const next = h.nextElementSibling;
        if (next && getComputedStyle(next).display !== 'none') h.click();
    }""")

    # Scroll to Messages section
    page.evaluate("""() => {
        document.querySelectorAll('.section-header')[2]
          .scrollIntoView({behavior: 'instant', block: 'start'});
    }""")
    time.sleep(0.3)
    page.screenshot(path="/tmp/codex-e2e/messages.png")

    browser.close()
```

## Verification Checklist

- [ ] Trace `.jsonl` has ≥2 `POST /v1/responses` entries
- [ ] Response status is 200 (not 401/502)
- [ ] Token counts are non-zero in Responses calls
- [ ] HTML viewer is generated (`trace_*.html`)
- [ ] Sidebar shows multiple calls with model name and token counts
- [ ] Messages section shows `user` message text (verifies #41 fix)
- [ ] Response section shows assistant reply (verifies #40 fix)

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| WebSocket 502 then HTTP 401 | Default target `api.openai.com` rejects ChatGPT OAuth tokens | Use `--tap-target https://chatgpt.com/backend-api/codex` |
| `Missing scopes: api.responses.write` | API key lacks Responses API access | Use OAuth (`codex auth login`) instead of `OPENAI_API_KEY` |
| Only 1 API call | Simple prompt completed in one round | Use a task requiring tool use (file reads, shell commands) |
| `OPENAI_BASE_URL is deprecated` warning | Codex v0.115+ prefers config.toml | Harmless — proxy still works via env var |

## Notes

- Codex with OAuth uses WebSocket first, then falls back to HTTP/SSE when proxied.
  The fallback is transparent — traces capture the HTTP/SSE path correctly.
- Each `codex exec` session also calls `GET /v1/models` for model discovery.
- The `--dangerously-bypass-approvals-and-sandbox` flag is required for non-interactive exec.
