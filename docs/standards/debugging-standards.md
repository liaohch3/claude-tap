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

## Post-Debug

10. **Write the experience doc.** Every non-trivial debugging session produces
    an entry in `docs/error-experience/entries/`. Include:
    - What broke
    - What you tried (and why it didn't work)
    - What actually fixed it
    - The lesson for future debugging
