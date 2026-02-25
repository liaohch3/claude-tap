# Mock E2E Test Pattern: Fake Upstream + Fake Claude

**Date:** 2026-02-25
**Tags:** testing, E2E, mock, best-practice

## Pattern Description

The existing E2E test suite (`tests/test_e2e.py`) uses a fully mocked approach
that tests the entire claude-tap pipeline without any external dependencies:

1. **Fake upstream server** — An aiohttp server running in a background thread
   that mimics the Anthropic API, returning both non-streaming and streaming (SSE)
   responses.

2. **Fake Claude script** — A temporary Python script placed in PATH that acts
   as the `claude` CLI. It makes HTTP requests to `ANTHROPIC_BASE_URL` (set by
   claude-tap to point at the proxy) and prints the results.

3. **Real claude-tap** — The actual `claude_tap` module is run as a subprocess
   with `--tap-target` pointing to the fake upstream.

## Why It Works Well

- **No external dependencies**: Tests run offline, no API keys needed
- **Deterministic**: Same input always produces the same output
- **Fast**: No network latency, no rate limits
- **Complete coverage**: Tests the full pipeline — proxy startup, request forwarding,
  SSE reassembly, JSONL recording, HTML viewer generation, API key redaction
- **Robust edge cases**: Includes tests for upstream errors (500), malformed SSE,
  and large payloads (100KB+)

## Key Implementation Details

- `run_fake_upstream_in_thread()` uses `threading.Event` for synchronization
- Fake Claude script is created with `_create_fake_claude()` and made executable
- Temporary bin directory is prepended to `PATH` so claude-tap finds the fake `claude`
- Port hardcoding (19199, 19200, etc.) keeps tests isolated
- Trace files are written to `tempfile.mkdtemp()` and cleaned up after assertions

## When to Use This Pattern

Use this pattern when:
- Testing proxy behavior (forwarding, recording, SSE handling)
- Testing HTML viewer generation
- Testing header redaction and security features
- Running in CI where no Claude API access is available

## Complementary Pattern

For testing real Claude integration (actual API responses, tool use, multi-turn
conversations), see the real E2E tests in `tests/e2e/`. Those require a working
`claude` CLI installation and are skipped by default in CI.
