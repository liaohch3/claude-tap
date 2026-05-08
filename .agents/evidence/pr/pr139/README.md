# PR 139 Kimi CLI Multi-Turn Evidence

Generated on 2026-05-08 from a real `claude-tap --tap-client kimi` run against a local streaming fake upstream.

## Trace Source

- JSONL: `.traces/kimi-pr139-continuous-1778226290/2026-05-08/trace_154450.jsonl`
- Viewer: `.traces/kimi-pr139-continuous-1778226290/2026-05-08/trace_154450.html`

## Coverage

- Continuous user turns: 5
- Chat Completions requests: 10
- Tool-call requests per turn: 1
- Final-answer requests per turn: 1
- Tool calls per tool-call request: 2
- Total tool calls: 10
- Unique response tools: `inspect_git`, `list_dir`, `parse_json`, `read_file`, `run_tests`, `search_code`
- System prompt coverage: every request starts with `Kimi CLI regression system prompt: keep one continuous session.`
- Multi-turn continuity: later requests replay previous assistant tool calls, tool result messages, final assistant answers, and later user messages in one accumulated Chat Completions history.
- Streaming coverage: each response streams `tool_calls` deltas and choice-level usage with cache-read tokens.
- Viewer regression coverage: historical assistant `tool_calls` render as tool-use content instead of empty assistant message blocks.

## Screenshots

- `kimi-multiturn-01-overview.png` - first turn overview with ten captured Kimi requests and System Prompt.
- `kimi-multiturn-02-expanded-tool-definitions.png` - expanded request tool definitions.
- `kimi-multiturn-03-turn3-history-scrolled.png` - scrolled third turn showing accumulated multi-turn history.
- `kimi-multiturn-04-turn5-response-tools-scrolled.png` - scrolled fifth turn tool-call response.
- `kimi-multiturn-05-final-answer-scrolled.png` - scrolled fifth turn final answer after tool results.
- `kimi-multiturn-06-full-json-sidebar-scrolled.png` - scrolled Full JSON with sidebar positioned at the final turn.

## Validation

```bash
UV_NO_SYNC=1 uv run python scripts/check_screenshots.py .agents/evidence/pr/pr139
UV_NO_SYNC=1 uv run python scripts/verify_screenshots.py .traces/kimi-pr139-continuous-1778226290/2026-05-08/trace_154450.html
```
