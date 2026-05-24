---
name: codex-e2e-test
description: Run PR-grade real Codex E2E validation through claude-tap, including resume turns, multiple tool calls, optional image input, viewer verification, and screenshot evidence.
tags: testing, e2e, codex, responses-api
---

# Codex E2E Test Skill

Run real end-to-end validation that starts `claude-tap` from local source,
connects to the real Codex CLI via OAuth, captures OpenAI Responses API traces,
and produces viewer screenshots suitable for PR evidence.

Use this skill for every PR that changes capture, proxying, viewer rendering,
session/dashboard behavior, client launch logic, trace ordering, content blocks,
tools, token usage, or screenshot/demo assets. If a PR cannot run this flow,
state why in the PR and cover the same risk with another real client trace.

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

Prefer the resume + multimodal flow below for PR evidence. The simple commands
are only smoke tests for checking local setup.

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

## Resume + Multimodal Content-Block Trace

Use this flow for viewer changes that affect message rendering, content block
boundaries, tool call ordering, images, or copy/select behavior. It creates a
real Codex session, resumes it at least once, forces multiple shell tool calls
per user turn, and attaches an actual image so the trace includes multimodal
content. This is the default PR evidence flow.

### 1. Prepare an isolated workspace

```bash
mkdir -p /tmp/claude-tap-real-codex-workspace
printf 'project = "claude-tap-real-codex-e2e"\n' \
  > /tmp/claude-tap-real-codex-workspace/project.toml
```

Create or copy a small valid PNG into the workspace. If you need a deterministic
local image, generate it with Python's standard library:

```bash
python3 - <<'PY'
from pathlib import Path
import struct
import zlib

def chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

width = height = 8
rows = b"".join(b"\x00" + (b"\x2f\x80\xed\xff" * width) for _ in range(height))
png = (
    b"\x89PNG\r\n\x1a\n"
    + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    + chunk(b"IDAT", zlib.compress(rows))
    + chunk(b"IEND", b"")
)
Path("/tmp/claude-tap-real-codex-workspace/input.png").write_bytes(png)
PY
```

### 2. Start a real Codex session through claude-tap

Run from the repository, but point Codex at the isolated workspace with `-C`.
The prompt should explicitly require several tool calls so the viewer has
enough messages, tool calls, and response blocks to inspect.

```bash
uv run claude-tap --tap-client codex \
  --tap-target https://chatgpt.com/backend-api/codex \
  --tap-output-dir /tmp/claude-tap-real-codex-traces \
  --tap-no-open --tap-no-update-check \
  -- exec -C /tmp/claude-tap-real-codex-workspace \
  --image /tmp/claude-tap-real-codex-workspace/input.png \
  --dangerously-bypass-approvals-and-sandbox \
  "Inspect this workspace and the attached image. Use shell tools to run pwd, list files, inspect project.toml, inspect input.png, then write codex_e2e_report.txt with your findings. Keep all writes inside this workspace."
```

### 3. Resume the same Codex session with another real turn

Use the session id printed by the first Codex run when possible. Avoid relying
on `--last` on busy maintainer machines because it can resume an unrelated
recent Codex session.

```bash
uv run claude-tap --tap-client codex \
  --tap-target https://chatgpt.com/backend-api/codex \
  --tap-output-dir /tmp/claude-tap-real-codex-traces \
  --tap-no-open --tap-no-update-check \
  -- exec resume <SESSION_ID_FROM_FIRST_RUN> \
  --image /tmp/claude-tap-real-codex-workspace/input.png \
  --dangerously-bypass-approvals-and-sandbox \
  "Continue the same investigation in /tmp/claude-tap-real-codex-workspace. Use shell tools to read /tmp/claude-tap-real-codex-workspace/codex_e2e_report.txt, compute the byte size of /tmp/claude-tap-real-codex-workspace/input.png, and write /tmp/claude-tap-real-codex-workspace/codex_e2e_followup.txt. Then summarize what changed since the previous turn."
```

### 4. Capture multi-position viewer screenshots

Take screenshots at multiple scroll positions, including a deeper position in
the same detail pane. Store them under `.agents/evidence/pr/<topic>/` and use
`raw.githubusercontent.com` links in the PR body.

```bash
mkdir -p .agents/evidence/pr/codex-real-e2e

uv run python - <<'PY'
from pathlib import Path
from playwright.sync_api import sync_playwright

html_files = sorted(Path("/tmp/claude-tap-real-codex-traces").rglob("trace_*.html"))
if not html_files:
    raise SystemExit("No viewer HTML found in /tmp/claude-tap-real-codex-traces")
html = html_files[-1]
out_dir = Path(".agents/evidence/pr/codex-real-e2e")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1760, "height": 1100}, device_scale_factor=1)
    page.goto(f"file://{html}", wait_until="domcontentloaded", timeout=10000)
    page.wait_for_selector(".sidebar-item", timeout=5000)
    page.evaluate("document.documentElement.setAttribute('data-theme', 'light')")

    # Select the last Responses call so resume context is visible.
    page.evaluate(
        """() => {
          const items = Array.from(document.querySelectorAll('.sidebar-item'));
          const responses = items.filter(item => item.textContent.includes('/v1/responses'));
          (responses.at(-1) || items.at(-1))?.click();
        }"""
    )
    page.wait_for_timeout(300)

    page.evaluate(
        """() => {
          for (const section of document.querySelectorAll('#detail .section')) {
            const title = section.querySelector('.title')?.textContent || '';
            const body = section.querySelector('.section-body');
            const header = section.querySelector('.section-header');
            if (!body || !header) continue;
            const shouldOpen = ['System Prompt', 'Messages', 'Response'].includes(title);
            const isOpen = body.classList.contains('open');
            if (shouldOpen !== isOpen) header.click();
          }
        }"""
    )
    page.wait_for_timeout(200)

    def shot(name: str, scroll_top: int) -> None:
        page.evaluate("y => { const d = document.querySelector('#detail'); if (d) d.scrollTop = y; }", scroll_top)
        page.wait_for_timeout(200)
        page.screenshot(path=str(out_dir / name), full_page=False)

    shot("codex-real-top.png", 0)
    shot("codex-real-mid.png", 700)
    shot("codex-real-deep.png", 1400)

    image_count = page.evaluate(
        """() => {
          const img = document.querySelector('#detail img');
          if (!img) return 0;
          img.scrollIntoView({ block: 'center', inline: 'nearest' });
          return document.querySelectorAll('#detail img').length;
        }"""
    )
    if image_count:
        page.wait_for_timeout(200)
        page.screenshot(path=str(out_dir / "codex-real-image.png"), full_page=False)

    browser.close()
PY
```

Validate screenshots:

```bash
uv run python scripts/check_screenshots.py .agents/evidence/pr/codex-real-e2e
```

### 5. Verify the trace

- The output directory contains at least two real `.jsonl` traces and generated
  `.html` viewers from `claude-tap`.
- The trace includes multiple `POST /v1/responses` entries with status 200 and
  non-zero token usage.
- At least one request contains image content from the `--image` attachment.
- The viewer shows Messages and Response sections with multiple content blocks
  separated cleanly.
- Tool calls and tool results stay interleaved in the order they happened.
- Copy buttons still copy the complete logical message/section text, not only
  one visual content block.
- Text selection across adjacent content blocks remains possible in the browser.
- Screenshot evidence includes at least two scroll depths for the same resumed
  trace, not only the top of the page.
- If the trace contains image input, screenshot evidence includes the rendered
  image block or records why the client did not send image content into the API
  request body.

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
