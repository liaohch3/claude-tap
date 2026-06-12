# Windows pip update evidence

`windows-session-update-prompt.png` records the output of the production
installer detection and automatic-update decision on Windows:

```powershell
.\.venv\Scripts\python.exe -c "import sys; from claude_tap.cli_update import _detect_installer, _maybe_start_background_update; print('Platform:', sys.platform); print('Python:', sys.executable); print('Detected installer:', _detect_installer()); print('Update available: 0.1.110 -> 99.0.0'); _maybe_start_background_update(no_auto_update=False)"
```

The command ran from the PR checkout in an isolated pip-installed virtual
environment. It exercises the startup update decision that runs before the
dashboard and trace viewer workflow. The output confirms that a Windows pip
installation receives manual update instructions instead of starting a
background pip process.

No API keys, prompts, trace records, or user data are present.
