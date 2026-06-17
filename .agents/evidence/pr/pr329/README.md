# PR 329 — Session Detail Back Button

Screenshot showing the `←` back button in `.detail-page-head` bar on the dashboard session detail page.

**Changes:**
- Back button in detail page header calls `showListView()`
- Clears `state.selectedSessionId` before returning to list (fixes stale-fetch race)

**Source:**
- Local dashboard at `http://localhost:3000` (via `claude-tap dashboard`)
- Real trace: session detail page with actual trace data from local SQLite store
