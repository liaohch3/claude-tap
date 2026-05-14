# PR 114 Evidence

This evidence captures a real dry run of the new update subcommand from this PR branch:

```bash
uv run claude-tap update --installer pip --dry-run
```

The command prints the foreground pip upgrade command without performing a network upgrade.
