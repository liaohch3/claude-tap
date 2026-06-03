Mobile-first UI evidence captured from a real trace database.

Source trace data:

- `.traces/pr269-newapi-bedrock-claude/traces.sqlite3`
- Session model: `bedrock/claude-sonnet-4-6`
- Session prompt: `Reply with exactly: real-bedrock-ok`

Local server:

```bash
CLOUDTAP_DB=.traces/pr269-newapi-bedrock-claude/traces.sqlite3 \
  uv run claude-tap dashboard \
  --tap-output-dir .traces/pr269-newapi-bedrock-claude \
  --tap-live-port 33117 \
  --tap-no-open
```

Screenshots:

- `dashboard-mobile-list-320.png`: dashboard session cards at 320px.
- `dashboard-mobile-list-375.png`: dashboard session cards at 375px.
- `dashboard-tablet-list-768.png`: tablet-width dashboard session cards at 768px.
- `viewer-mobile-list-375.png`: standalone viewer list state at 375px.
- `viewer-mobile-detail-320.png`: standalone viewer detail state at 320px.
- `viewer-mobile-detail-375.png`: standalone viewer detail state at 375px.

All captured viewport checks reported `overflowX: 0`.
