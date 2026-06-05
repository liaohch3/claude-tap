# PR 291 Dashboard Evidence

This evidence validates the dashboard quit control and same-origin quit-token flow.

- Screenshot: `dashboard-quit-token.png`
- Source: temporary real JSONL trace at `/private/tmp/claude-tap-pr291-dashboard/traces/2026-06-05/trace_141500.jsonl`
- Capture: dashboard server started with `LiveViewerServer(dashboard_mode=True)` after migrating the temporary trace into a temporary SQLite database.
- Validation: `uv run python scripts/check_screenshots.py .agents/evidence/pr/pr291/dashboard-quit-token.png`

The raw trace and SQLite database are intentionally not committed.
