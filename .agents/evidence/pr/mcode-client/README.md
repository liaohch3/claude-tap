# MiniMax Code Client Evidence

Source: real MiniMax Code 0.1.2 run through this branch's filtered forward
proxy using the locally authenticated managed MiniMax provider.

- Trace session: `d62afb3b-9673-4ac2-bfce-d2fb85916d04`
- Proxy mode: `forward`
- Model: `MiniMax-M3`
- Captured model calls: 3
- MCode exit code: 0
- Expected and observed response: `MCODE_TAP_OK`
- Exported viewer: `/tmp/claude-tap-mcode-real.FlkuJc/trace.html` (local only)
- Screenshot: `mcode-real-e2e-trace-viewer.png`

The run used the safe prompt `Reply exactly MCODE_TAP_OK.`. The trace database,
JSON export, and HTML viewer remain local and are not committed. The screenshot
shows the final response and aggregate token accounting without authentication
headers or user workspace content.

Validation commands:

```bash
uv run python scripts/check_screenshots.py \
  .agents/evidence/pr/mcode-client/mcode-real-e2e-trace-viewer.png

uv run python scripts/verify_screenshots.py \
  /tmp/claude-tap-mcode-real.FlkuJc/trace.html
```
