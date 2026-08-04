# Cursor transcript-only dashboard evidence

## Problem shape

Cursor Agent sessions are captured from local `agent-transcripts/*.jsonl` (no
MITM proxy). The dashboard must show one Cursor session per transcript, with
readable first message, model enrichment, and `/cursor/transcript/...` turn
paths.

## Deterministic recreation

From the repo root:

```bash
uv run python .agents/evidence/pr/418-cursor-transcript-only/seed_and_capture.py
uv run python scripts/check_screenshots.py .agents/evidence/pr/418-cursor-transcript-only/
```

The seed script:

1. Builds a local store at `.traces/418-cursor-transcript-only/traces.sqlite3`
   through `TraceStore.create_session()` / `append_record()` / `finalize_session()`
   with `client=cursor`, `proxy_mode=transcript`, and three cursor-transcript turns
2. Opens `LiveViewerServer(dashboard_mode=True)` and captures:
   - sessions list (Cursor agent, `grok-4.5`, first message)
   - session detail timeline (`/cursor/transcript/...` paths)

`.traces/` remains gitignored; reviewers reproduce the store with the committed
`seed_and_capture.py` script above.

## Artifacts

- `seed_and_capture.py`: deterministic seed + Playwright capture
- `dashboard-cursor-sessions.png`: Conversation Log row for the Cursor session
- `dashboard-cursor-session-detail.png`: Cursor details turns with transcript paths
