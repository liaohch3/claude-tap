# Issue 129 DeepSeek Token Label Validation

Date: 2026-05-07

This evidence validates the viewer wording change from PR #133 against a real
Claude Code session routed through the DeepSeek Anthropic-compatible API with
`claude-tap`.

## Environment

- Claude Code: 2.1.132
- Upstream target: `https://api.deepseek.com/anthropic`
- claude-tap mode: reverse proxy
- Trace source: `.traces/deepseek-issue129-manual-1778145043/2026-05-07/trace_091044.jsonl`
- HTML source: `.traces/deepseek-issue129-manual-1778145043/2026-05-07/trace_091044.html`

The trace files are local evidence and are not committed because they may
contain prompt or environment context. Authentication headers are redacted by
claude-tap.

## Prompt Coverage

The run used three interactive Claude Code prompts. Each prompt requested Bash
tool use and a marker in the final response:

- `TOOL_ROUND_ONE_OK`
- `TOOL_ROUND_TWO_OK`
- `TOOL_ROUND_THREE_OK`

## Observed Trace Summary

- Records: 8
- `/v1/messages` requests: 8
- `tool_use` text occurrences: 32
- `tool_result` text occurrences: 9
- Input tokens: 24,366
- Output tokens: 1,550
- Total API tokens (`input_tokens + output_tokens`): 25,916
- Cache read input tokens: 119,040
- Cache creation input tokens: 0

## Screenshots

- `deepseek-real-token-summary.png` shows the real DeepSeek trace with the
  updated `累计 API Token` header wording.
- `deepseek-real-tool-dialogue.png` shows the same real trace after selecting a
  later request with tool/dialogue context.

## Validation Commands

```bash
uv run python scripts/check_screenshots.py \
  .agents/evidence/pr/issue129/deepseek-real-token-summary.png \
  .agents/evidence/pr/issue129/deepseek-real-tool-dialogue.png

uv run python scripts/verify_screenshots.py \
  .traces/deepseek-issue129-manual-1778145043/2026-05-07/trace_091044.html
```
