Mobile-first UI evidence captured from a real Claude Code trace.

Source trace data:

- Local trace database: `.traces/mobile-first-ui/traces.sqlite3`
- Session id: `600fd026-dbab-4d44-b19f-8df27536398d`
- Session model: `claude-sonnet-4-6`
- Session prompt: `Reply exactly: mobile-layout-real-trace-ok`
- API records: 2

Trace capture:

```bash
CLOUDTAP_DB=.traces/mobile-first-ui/traces.sqlite3 \
  timeout 45s uv run python -m claude_tap \
  --tap-no-live \
  --tap-no-open \
  --tap-no-update-check \
  --tap-output-dir .traces/mobile-first-ui/out \
  -- -p --tools '' --no-session-persistence --max-budget-usd 0.03 \
  'Reply exactly: mobile-layout-real-trace-ok'
```

Local dashboard server:

```bash
CLOUDTAP_DB=.traces/mobile-first-ui/traces.sqlite3 \
  uv run claude-tap dashboard \
  --tap-output-dir .traces/mobile-first-ui/out \
  --tap-live-port 33117 \
  --tap-no-open
```

Screenshots:

- `dashboard-mobile-list-320.png`: dashboard session cards at 320px.
- `dashboard-mobile-actions-320.png`: dashboard mobile card action row at 320px.
- `dashboard-mobile-list-375.png`: dashboard session cards at 375px.
- `dashboard-tablet-list-768.png`: tablet-width dashboard session cards at 768px.
- `dashboard-desktop-table-1440.png`: desktop dashboard table at 1440px.
- `viewer-mobile-list-320.png`: standalone viewer list state at 320px.
- `viewer-mobile-list-375.png`: standalone viewer list state at 375px.
- `viewer-mobile-detail-320.png`: standalone viewer detail state at 320px.
- `viewer-mobile-detail-375.png`: standalone viewer detail state at 375px.
- `viewer-desktop-detail-1440.png`: standalone viewer detail state at 1440px.

Capture assertions:

- `/api/sessions` returned exactly the generated session.
- Every captured viewport reported page-level `overflowX <= 2`.
- Dashboard mobile refresh control renders as an icon with `aria-label="Refresh"`, not a literal `R`.
- Viewer mobile detail action buttons stay inside the action bar, including `Diff with Prev`.
- Viewer mobile detail action bar stays below the sticky detail inspector tabs after scrolling.
- Empty embedded traces keep the sidebar hidden at mobile width.
- Viewer detail evidence keeps long `System Prompt` and `Messages` sections collapsed to avoid exposing local paths or private prompt context in public PR screenshots.

Manual screenshot review:

- No mobile screenshot shows clipped primary actions.
- No mobile detail screenshot shows action buttons hidden under the detail inspector tabs.
- No screenshot shows synthetic or mock trace data.
- No screenshot exposes secrets, tokens, local email addresses, or expanded long prompt context.
