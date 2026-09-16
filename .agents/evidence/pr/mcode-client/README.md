# MiniMax Code Client Evidence

Source: a real interactive MiniMax Code 0.1.2 TUI session launched through
this branch's filtered forward proxy with the locally authenticated managed
MiniMax provider.

- Trace session: `76243671-fbdf-430e-9175-989e33b3ea97`
- Proxy mode: `forward`
- Client identity stored by claude-tap: `mcode` (`MiniMax Code` in dashboard)
- Model: `hy-5.6-sol`
- Captured model calls: 3
- Session status after `/exit`: `complete`
- Screenshot: `mcode-real-e2e-trace-viewer.png`

The same interactive TUI conversation completed two user turns:

1. `Remember the codeword ORBIT-731 and reply exactly: STORED ORBIT-731`
   returned `STORED ORBIT-731`.
2. `What codeword did I ask you to remember? Reply exactly: RECALL ORBIT-731`
   returned `RECALL ORBIT-731`.

The isolated SQLite database confirmed one completed `mcode` session with three
captured model records, and all three records contained the safe codeword. The
screenshot is the real dashboard summary for that database. It shows the
`MiniMax Code` client identity, three traces, the actual model, successful
status, and the safe first-message preview without exposing request headers,
authentication data, system context, or user workspace content.

The database, raw trace records, and tmux log remain under an isolated `/tmp`
directory and are not committed.

Validation command:

```bash
uv run python scripts/check_screenshots.py \
  .agents/evidence/pr/mcode-client/mcode-real-e2e-trace-viewer.png
```
