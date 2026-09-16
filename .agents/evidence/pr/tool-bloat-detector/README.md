# Tool output size detector evidence

## What the change does

Flags tool results large enough to dominate a turn's context: a banner above the
result in the detail view, and a badge on the sidebar entry showing the largest
result in that turn. Sizes are measured in UTF-8 bytes, threshold 10,000 bytes.

## Real-trace recording

Recorded with a live `claude-tap` run against Claude Code, prompting it to read
two large repo files so the follow-up request carries oversized `tool_result`
blocks:

```bash
uv run python -m claude_tap --tap-client claude \
  --tap-output-dir .traces/tool-bloat-evidence --tap-no-open --tap-no-live \
  -- -p "Read the file claude_tap/viewer_assets/renderers.js in full, then read claude_tap/viewer_assets/sidebar.js in full, then tell me the total line count of both files added together. Use the Read tool for both." \
  --permission-mode acceptEdits
```

The run produced session `771a5583-4fe6-466c-823f-27fded388a0b`: 4 API calls,
76,844 input tokens, two `Read` tool calls. Its records were exported to
`.traces/tool-bloat-evidence/trace_tool_bloat.jsonl` and rendered to
`trace_tool_bloat.html`.

Capture and verify:

```bash
uv run python .agents/evidence/pr/tool-bloat-detector/capture.py \
  .traces/tool-bloat-evidence/trace_tool_bloat.jsonl
uv run python scripts/verify_screenshots.py .traces/tool-bloat-evidence/trace_tool_bloat.html
python3 scripts/check_screenshots.py .agents/evidence/pr/tool-bloat-detector/
```

`.traces/` remains gitignored. Screenshots are committed under this directory.

## Artifacts

- `capture.py`: viewer HTML generation plus Playwright capture
- `trace-viewer-tool-bloat-sidebar-badge.png`: sidebar entry for the fourth API
  call carrying `⚠ 53.6KB`, the largest of its two oversized results; the three
  turns without oversized results carry no badge
- `trace-viewer-tool-bloat-detail-banner.png`: same entry expanded, banner
  `Large tool output: 41.3 KB (~10,585 tok)` above the `renderers.js` read
  result. The second banner (53.6 KB, the `sidebar.js` read) is further down the
  same turn.

Token counts on the banner are a deliberately coarse 4-bytes-per-token estimate
and are labelled approximate; the KB figure is exact.
