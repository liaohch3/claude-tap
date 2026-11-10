# Recorded header masking evidence

The screenshots show the actual claude-tap viewer rendering a fresh SQLite
session captured through the reverse HTTP proxy. A loopback fake upstream
handles two turns of one conversation: a JSON response followed by an SSE
response. All authentication values are inert sentinels; no provider account
or existing capture is used.

- `trace-viewer-masked-headers.png` shows the captured request's
  `Authorization` and `x-api-key` fields as `***` in the viewer's Full JSON
  section.
- `trace-viewer-multiturn.png` shows the second turn, its conversation context,
  reconstructed response, and six captured SSE events.

`capture_header_redaction.py` sends both requests through `proxy_handler` and
`TraceWriter`, verifies the upstream receives the original header values, and
checks request and response headers loaded from SQLite. It exports the
captured records to a self-contained viewer HTML file before taking screenshots
at 1440 by 1000 pixels. It also scans the fresh SQLite files and JSONL, compact,
log, and HTML exports for complete sentinels and their 12-character prefixes.

With the development dependencies and Playwright Chromium installed, reproduce
the capture from the repository root:

```bash
PYTHONPATH=. uv run python .agents/evidence/pr/480/capture_header_redaction.py
uv run python scripts/check_screenshots.py .agents/evidence/pr/480/
```

Each execution creates a new `.traces/pr480-header-redaction-*` directory and
prints its path. The directory contains `traces.sqlite3`, `trace.jsonl`,
`trace.ctap.json`, `proxy.log`, and `trace.html`. Raw captures and generated HTML
remain uncommitted. The screenshot script verifies visible viewer content,
masked header values, and the absence of JavaScript errors; it uses ordinary
viewer clicks without modifying the rendered page.
