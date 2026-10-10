# Issue #455 SIGHUP evidence

The screenshot was captured from a real Codex CLI run through the local PR branch and claude-tap reverse proxy.

- Command: `CLOUDTAP_DB=/tmp/claude-tap-sighup-evidence/traces.sqlite3 uv run python -m claude_tap --tap-client codex --tap-no-live --tap-no-open --tap-output-dir /tmp/claude-tap-sighup-evidence -- exec --sandbox read-only --skip-git-repo-check 'Reply with exactly: SIGHUP_EVIDENCE_OK'`
- Client result: `SIGHUP_EVIDENCE_OK`, exit code 0
- Real trace session: `2a912a5e-34c3-47f1-b2d3-e241c0a9ca89`
- Captured API calls: 2
- Local trace database: `/tmp/claude-tap-sighup-evidence/traces.sqlite3` (not committed)
- Exported viewer: `/tmp/claude-tap-sighup-evidence/viewer.html` (not committed)
- Screenshot: `client-trace-viewer.png`

The trace-viewer screenshot is a real client-capture smoke check. The SIGHUP termination, bounded escalation, exceptional cleanup, and signal restoration behavior are proved by `tests/test_client_signals.py`.
