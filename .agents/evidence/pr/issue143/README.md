# Issue 143 Codex Cached Tokens Evidence

Date: 2026-05-09

This evidence validates the Codex / OpenAI Responses cache-read token mapping
against a real Codex CLI run captured through `claude-tap`.

## Environment

- Codex CLI: 0.125.0
- claude-tap mode: reverse proxy
- Upstream target: `https://chatgpt.com/backend-api/codex`
- Trace source: `.traces/issue143-codex-cache/2026-05-09/trace_093315.jsonl`
- HTML source: `.traces/issue143-codex-cache/2026-05-09/trace_093315.html`

The trace files are local evidence and are not committed because they may
contain request headers or prompt context. Authentication headers are redacted
by `claude-tap`.

## Observed Trace Summary

- API calls: 2
- Responses call: 1
- Input tokens: 15,862
- Output tokens: 9
- Cache read tokens: 7,552

## Screenshots

- `codex-cached-tokens.png` shows the real Codex Responses trace with
  `input_tokens_details.cached_tokens` displayed as `Cache Read` in both the
  header summary and the selected trace token bar.

## Validation Commands

```bash
uv run python scripts/check_screenshots.py \
  .agents/evidence/pr/issue143/codex-cached-tokens.png

uv run python scripts/verify_screenshots.py \
  .traces/issue143-codex-cache/2026-05-09/trace_093315.html
```
