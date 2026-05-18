# CodeBuddy CLI PR Evidence

Generated from a real CodeBuddy CLI run on 2026-05-24 against the iOA login deployment.

## Command

```bash
uv run claude-tap --tap-live --tap-client codebuddy
# Then in the launched CodeBuddy session: type `hi`
```

The reverse proxy auto-detected the upstream from CodeBuddy's login cache
(`~/.codebuddy/local_storage/entry_933d5543e80177622c17a73869c0fad7.info`)
and resolved to `https://copilot.tencent.com/v2`.

## Trace artifacts

- Trace JSONL: `.traces/2026-05-24/trace_224112.jsonl`
- Trace HTML: `.traces/2026-05-24/trace_224112.html`
- Live viewer URL: `http://127.0.0.1:45109`
- Local proxy port: `http://127.0.0.1:44667`
- Cumulative API tokens: 76,176 (3 turns: Plan + Subagent ×2)

## Screenshots

| File | Field coverage |
|------|----------------|
| `01-overview.png` | System Prompt + Messages + Response + turn list (OPUS-4.7-1M / HAIKU-4.5 with subagents). Single screenshot showing live viewer side by side with the running CodeBuddy CLI terminal. |
| `02-tools.png` | Tools schema panel (expanded) showing the JSON tool definitions CodeBuddy sends to the LLM. |

Both screenshots are ≥1280px viewport and use real trace data (no mocks).

## Validation

```bash
uv run ruff check claude_tap/cli.py tests/test_codebuddy_launch.py
uv run ruff format --check claude_tap/cli.py tests/test_codebuddy_launch.py
uv run pytest tests/ -x --timeout=60          # 443 passed, 25 skipped
```
