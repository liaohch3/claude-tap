# Windows pip update evidence

`dashboard-session.png` shows the real dashboard session created by the PR
checkout on Windows. The session used an isolated local database and custom
dashboard port:

```powershell
$env:CLOUDTAP_DB = "D:\projects\goal\.tmp\pr319-evidence\claude-tap.sqlite3"
.\.venv\Scripts\python.exe -m claude_tap --tap-no-launch --tap-no-open `
  --tap-live-port 31927 `
  --tap-output-dir D:\projects\goal\.tmp\pr319-evidence\.traces
```

The screenshot was captured from `http://127.0.0.1:31927` after the production
startup path created the session and spawned the shared dashboard. It confirms
that the dashboard process is active at the custom address that the update
instructions must target. The isolated database and raw session data are not
committed.

No API keys, prompts, or user data are present.
