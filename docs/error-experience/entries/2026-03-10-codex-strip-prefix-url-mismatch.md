# Codex strip_path_prefix URL mismatch

Date: 2026-03-10

## What broke

Codex CLI through claude-tap proxy returned 404 (HTTP) and 502 (WebSocket) for all requests.

## Root cause

`strip_path_prefix="/v1"` was set for the codex client, but `default_target` was `https://api.openai.com` (without `/v1`). This caused:

- Codex sends `/v1/responses`
- Proxy strips `/v1` → path becomes `/responses`
- Upstream URL: `https://api.openai.com/responses` ← wrong, should be `/v1/responses`

## What we tried

### Attempt 1: Remove strip_path_prefix entirely

Set `strip_path_prefix=""` for all codex clients. This fixed `api.openai.com` (API Key auth) but broke `chatgpt.com/backend-api/codex` (OAuth auth), because that backend expects `/responses` without `/v1`.

**Why it failed:** Only considered one authentication mode. Codex has two different backends with different URL structures:

| Backend | Expected path | Needs strip? |
|---------|-------------|-------------|
| `api.openai.com` | `/v1/responses` | No |
| `chatgpt.com/backend-api/codex` | `/responses` | Yes |

### Attempt 2: Conditional strip based on target URL

```python
"strip_path_prefix": "/v1" if args.client == "codex" and "api.openai.com" not in args.target else ""
```

This correctly handles both backends.

## What actually fixed it

The conditional strip (attempt 2), combined with tmux-based real E2E verification against the live chatgpt.com backend.

## Why the bug wasn't caught originally

1. **E2E test validated internal consistency, not external correctness.** The fake upstream handler asserted `request.path == "/messages"` (the stripped path), which confirmed stripping worked — but never checked if the constructed upstream URL matched the real API.

2. **Failure was misattributed to environment.** WS_VERIFY_REPORT.md documented WS 502 failures but concluded "environment/network dependent WS reachability" instead of investigating the URL construction.

3. **No real-endpoint URL assertion.** No test verified that `target + stripped_path` produces a valid real API URL.

## Lessons

1. **For proxy code, test the final upstream URL, not just the forwarded path.** Add assertions like:
   ```python
   assert upstream_url == "https://api.openai.com/v1/responses"
   ```

2. **When a client supports multiple backends, enumerate all (client × auth × target) combinations** before modifying URL handling logic. Draw a matrix.

3. **Don't attribute failures to "environment" without first verifying the code path.** Print/log the actual upstream URL to confirm correctness before blaming network.

4. **Always run real E2E after proxy changes.** Unit tests with fake upstreams give false confidence for networking code. Use `tmux` to run a real request through the proxy.
