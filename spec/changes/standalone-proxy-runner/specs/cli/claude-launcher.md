# Claude Code Launcher

## Requirement

The standalone CLI must launch Claude Code through the local proxy while preserving Claude Code TUI behavior, process exit code, and configured upstream target. It must support reverse proxy injection through `ANTHROPIC_BASE_URL` and Claude settings payloads, and forward proxy injection through standard proxy environment variables when requested.

## Scenarios

### Scenario: Reverse proxy launch succeeds

**WHEN** the user runs the standalone tool for Claude Code in reverse mode
**THEN** the child `claude` process receives `ANTHROPIC_BASE_URL=http://127.0.0.1:<port>`
**AND** the local reverse proxy forwards `/v1/messages` traffic to the detected upstream target.

### Scenario: Existing Claude settings target is honored

**WHEN** `ANTHROPIC_BASE_URL` is present in environment or Claude settings
**THEN** the standalone tool uses that as the upstream target
**AND** it still points the child process at the local proxy.

### Scenario: Claude binary missing

**WHEN** `claude` is not found in `PATH`
**THEN** the command exits non-zero with a concise installation hint
**AND** no proxy process remains running.

## Interface

### Props (if UI component)

Not applicable.

### API Contract (if endpoint)

| Surface | Method | Request | Response |
|---------|--------|---------|----------|
| CLI | command | `coding-cli claude -- [claude args]` | Exit code from child process |
| Launcher | function | `run_client(client="claude", proxy_mode, extra_args)` | Child exit code |
| Target detection | function | environment/settings paths | Anthropic upstream URL |

## Persistence (if applicable)

| Storage | Key | Value | Lifecycle |
|---------|-----|-------|-----------|
| Trace directory | `trace_*.jsonl` | Captured Claude Code API records | Appended during run |
| Trace directory | `trace_*.log` | Proxy/session diagnostics | Appended during run |

---
*Spec for: standalone-proxy-runner*
*Created: 2026-05-20T22:04:03Z*
