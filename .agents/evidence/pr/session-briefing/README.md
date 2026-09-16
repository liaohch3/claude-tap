# Session briefing evidence

Screenshots come from real local traces, rendered with the branch viewer.

| File | Trace | What it shows |
|---|---|---|
| `trace-viewer-session-briefing-cache.png` | `.traces/cache-invalidation-diagnostics/trace_cache_diagnostics.jsonl` | Cost and the cache-break line on a 138-turn capture |
| `trace-viewer-session-briefing-tools.png` | `.traces/tool-bloat-evidence/trace_tool_bloat.jsonl` | Largest tool-result line from a capture with oversized Read output |

Regenerate:

```bash
uv run python .agents/evidence/pr/session-briefing/capture.py \
  /path/to/claude-tap/.traces/cache-invalidation-diagnostics/trace_cache_diagnostics.jsonl \
  trace-viewer-session-briefing-cache.png
uv run python .agents/evidence/pr/session-briefing/capture.py \
  /path/to/claude-tap/.traces/tool-bloat-evidence/trace_tool_bloat.jsonl \
  trace-viewer-session-briefing-tools.png
```
