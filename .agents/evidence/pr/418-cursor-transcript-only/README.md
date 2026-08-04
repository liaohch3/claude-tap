# Cursor transcript-only dashboard evidence

## Problem shape

Cursor Agent sessions are captured from local `agent-transcripts/*.jsonl` (no
MITM proxy). The dashboard must show one Cursor session per transcript, with
readable first message, optional model enrichment, and `/cursor/transcript/...`
turn paths.

## Real-trace recreation

From the repo root (requires the source Cursor transcript on this machine):

```bash
uv run python .agents/evidence/pr/418-cursor-transcript-only/seed_and_capture.py
uv run python scripts/check_screenshots.py .agents/evidence/pr/418-cursor-transcript-only/
```

The seed script:

1. Copies the real nested transcript
   `~/.cursor/projects/Users-youngcan-claude-tap/agent-transcripts/0b95c4b6-03e2-4780-8c3a-124f43625297/0b95c4b6-03e2-4780-8c3a-124f43625297.jsonl`
   into `.traces/418-cursor-transcript-only/cursor-home/`
2. Imports it through `import_cursor_transcripts()` into
   `.traces/418-cursor-transcript-only/traces.sqlite3`
3. Opens `LiveViewerServer(dashboard_mode=True)` and captures the sessions list
   plus session detail timeline

`.traces/` remains gitignored. Screenshots are committed under this directory.

## Artifacts

- `seed_and_capture.py`: real-transcript stage + import + Playwright capture
- `dashboard-cursor-sessions.png`: Conversation Log row for the Cursor session
- `dashboard-cursor-session-detail.png`: Cursor details turns with transcript paths
