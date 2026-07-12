# Windows Command Shim Evidence

Source: real Codex CLI 0.144.0 run through the PR branch using the local
ChatGPT subscription and the reverse proxy target
`https://chatgpt.com/backend-api/codex`.

- Trace session: `16cc8654-901d-41c0-b177-6f09d3ea0e53`
- Captured turns: 6
- Codex exit code: 0
- Exported viewer: `/tmp/windows-shim-codex-e2e.html` (local only)
- Screenshot: `codex-real-trace.png`
- Windows reproduction: <https://github.com/liaohch3/claude-tap/actions/runs/29195283902>
- Windows success matrix: <https://github.com/liaohch3/claude-tap/actions/runs/29195896481>

The prompt required Codex to inspect the worktree, `pyproject.toml`, and the
latest two commits with shell tools, then report the project metadata and the
Windows fix without modifying files.
