# PR #335 — macOS monitor safety-chain E2E evidence

Addresses maintainer @liaohch3's remaining merge condition: real validation of
the macOS monitor inject → capture → restore → force-quit-recovery flow.

## Automated (isolated `$HOME`) — `e2e_safety_chain.py`

Exercises the **real** `claude_tap.global_inject` code and the **real**
`claude-tap monitor-restore` CLI entrypoint against a throwaway `$HOME`, so the
developer's real `~/.claude` / `~/.codex` are never modified.

```
uv run python .agents/evidence/pr/335/e2e_safety_chain.py   # -> 22/22 checks passed
```

Full output: [`e2e_safety_chain.log`](./e2e_safety_chain.log).

| Maintainer step | Covered | Evidence |
|---|---|---|
| 3. `~/.claude/settings.json` + `~/.codex/config.toml` injected with local proxy URLs | ✅ | both files point at `127.0.0.1:<port>`; pre-existing keys preserved; custom Codex provider `base_url` rerouted too (not just legacy `openai_base_url`) |
| 5. Stop Monitor → both files restore byte-for-byte | ✅ | sha256 + mode equal to originals; `.tap-backup` files removed; state cleared |
| 6. Force-quit while active → `monitor-restore` recovers files **and** processes | ✅ | after a simulated force-quit (injection + state left on disk), `claude-tap monitor-restore` restores both files byte-for-byte and terminates the recorded orphan proxy |

Also confirmed: `monitor-state.json` and config backups are created with
restrictive `0o600` permissions.

## Requires a manual macOS GUI run (out of scope for headless harness)

- Step 1–2: build the `.app` bundle, launch from Finder, click **Start Monitor**
  and accept the confirmation dialog.
- Step 4: launch fresh Claude Code and Codex sessions and confirm the dashboard
  captures both.

The confirmation gate itself is covered by unit tests
(`tests/test_macos_app.py`): every start path — Start Monitor, Open Dashboard
(from stopped), and launch auto-start — routes through `_confirm_start_monitor()`.
