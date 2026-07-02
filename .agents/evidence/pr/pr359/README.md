# PR 359 dashboard evidence

Source: real macOS menu monitor E2E trace database at `.traces/macos-menu-e2e-20260629-144944/traces.sqlite3`.

Screenshot:

- `dashboard-macos-menu-e2e.png` captures the dashboard rendered from that database with `CLOUDTAP_DB` pointed at the source SQLite file.

Validation:

- `uv run python scripts/check_screenshots.py .agents/evidence/pr/pr359`
