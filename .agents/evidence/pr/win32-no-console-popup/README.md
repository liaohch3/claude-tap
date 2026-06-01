# Evidence: Windows subprocess CREATE_NO_WINDOW fix

## What changed

`_start_background_update()` and `update_main()` in `claude_tap/cli_update.py`
now pass `subprocess.CREATE_NO_WINDOW` on Windows to prevent a visible Python
console window from flashing during background auto-updates or explicit
`claude-tap update` runs.

## Evidence

- **win32-trace-evidence.png**: pytest output showing all 4 Windows branch
  tests passing — verifying `CREATE_NO_WINDOW` is applied on `sys.platform ==
  "win32"` and absent on posix.
