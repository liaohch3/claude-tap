---
owner: claude-tap-maintainers
last_reviewed: 2026-03-03
source_of_truth: AGENTS.md
---

# Debugging Standards

## Pre-Debug Checklist

Before spawning agents or running automated retry loops:

1. **Read the code path** (5 minutes max). Trace from the entry point to the
   failing operation. Look for hardcoded values, skipped parameters, or
   divergent paths.

2. **Compare working vs broken.** If feature A works but feature B doesn't,
   write down the differences in their code paths. The bug is in the diff.

3. **Check the obvious.** Proxy settings, environment variables, port numbers,
   feature flags. Most bugs are configuration, not logic.

## During Debugging

4. **2-strike rule.** If the same approach (re-run tests, try different flags)
   fails twice, STOP. Switch to a different strategy:
   - Add targeted logging/print statements
   - Read the source code of the failing library
   - Reduce to minimal reproduction
   - Ask: "What assumption am I making that could be wrong?"

5. **No infinite loops.** Cron monitoring is for watching known-good processes.
   It is not a debugging tool. If a cron loop hasn't produced progress in
   2 cycles, disable it and debug manually.

6. **Log your hypotheses.** Before each attempt, write down:
   - What you think the problem is
   - What evidence would confirm/deny it
   - What you'll try
   This prevents circular reasoning and repeated attempts.

## Network/Proxy Debugging Specifically

7. **Always check the actual connect call parameters.** When debugging proxy
   issues, find the line where the connection is made and verify:
   - Is `proxy=` set? To what?
   - Is `trust_env=` True?
   - Are environment variables (`HTTP_PROXY`, `HTTPS_PROXY`) being read?

8. **Use the simplest possible test.** Before complex E2E:
   ```bash
   # Can we reach the host through proxy?
   curl -x http://127.0.0.1:7897 https://target-host/
   # Can we reach it directly?
   curl https://target-host/
   ```

9. **Check for proxy bypass.** Libraries often have `NO_PROXY`, `proxy=None`,
   or per-request proxy overrides that silently skip the system proxy.

## Proxy URL Construction Verification

10. **Verify the final upstream URL, not just the forwarded path.** When
    modifying path stripping, target URL, or route logic, add an assertion or
    log line that prints the fully constructed upstream URL. Confirm it matches
    the real API endpoint. Fake upstreams in unit tests only verify internal
    consistency — they cannot catch URL mismatches with real APIs.

11. **Enumerate all (client × auth × target) combinations.** Before changing
    URL handling, draw a matrix of every supported configuration:

    ```
                  api.openai.com    chatgpt.com/backend-api/codex
    strip /v1     ✗ 404             ✓
    no strip      ✓                 ✗ wrong path
    conditional   ✓                 ✓  ← correct
    ```

    Every cell must be verified — either by automated test or real E2E run.

12. **Run real E2E after any proxy/routing change.** Unit tests with fake
    upstreams are necessary but not sufficient. After proxy changes, run at
    least one real request through the proxy using tmux:

    ```bash
    # Example: verify Codex through proxy
    tmux new-session -d -s verify \
      "uv run python -m claude_tap --tap-client codex --tap-target TARGET --tap-no-launch --tap-port 0"
    # Then launch client in another window and send a test message
    ```

13. **Don't attribute failures to "environment" without evidence.** When a
    request fails through the proxy, first print/log the constructed upstream
    URL. Only blame network/environment after confirming the URL is correct.
    See: `docs/error-experience/entries/2026-03-10-codex-strip-prefix-url-mismatch.md`

## Post-Debug

14. **Write the experience doc.** Every non-trivial debugging session produces
    an entry in `docs/error-experience/entries/`. Include:
    - What broke
    - What you tried (and why it didn't work)
    - What actually fixed it
    - The lesson for future debugging
