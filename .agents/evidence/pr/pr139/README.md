# PR 139 Kimi CLI Multi-Turn Evidence

Generated on 2026-05-08 from a real `claude-tap --tap-client kimi` run against a local streaming fake upstream.

## Trace Source

- JSONL: `.traces/kimi-pr139-multiturn-1778225276/2026-05-08/trace_152756.jsonl`
- Viewer: `.traces/kimi-pr139-multiturn-1778225276/2026-05-08/trace_152756.html`

## Coverage

- Chat Completions requests: 5
- Tool calls per request: 2
- Total tool calls: 10
- Unique response tools: `inspect_git`, `list_dir`, `parse_json`, `read_file`, `run_tests`, `search_code`
- Multi-turn continuity: each later request replays previous assistant tool calls and tool result messages in request history.
- Streaming coverage: each response streams `tool_calls` deltas and choice-level usage with cache-read tokens.

## Screenshots

- `kimi-multiturn-01-overview.png` - first turn overview with five captured Kimi requests.
- `kimi-multiturn-02-expanded-tool-definitions.png` - expanded request tool definitions.
- `kimi-multiturn-03-turn3-history-scrolled.png` - scrolled third turn showing accumulated multi-turn history.
- `kimi-multiturn-04-turn5-response-tools-scrolled.png` - scrolled fifth turn response with streamed tool calls.
- `kimi-multiturn-05-sse-events-scrolled.png` - scrolled SSE event detail.
- `kimi-multiturn-06-full-json-sidebar-scrolled.png` - scrolled Full JSON with sidebar positioned at the final turn.

## Validation

```bash
UV_NO_SYNC=1 uv run python scripts/check_screenshots.py .agents/evidence/pr/pr139
UV_NO_SYNC=1 uv run python scripts/verify_screenshots.py .traces/kimi-pr139-multiturn-1778225276/2026-05-08/trace_152756.html
```
