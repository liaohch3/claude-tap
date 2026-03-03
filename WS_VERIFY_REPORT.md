# PR #22 WebSocket Strict Validation Report

Date: 2026-03-03
PR: https://github.com/liaohch3/claude-tap/pull/22
Branch: `feat/ws-proxy`
Workspace: `/private/tmp/claude-tap-pr22-ws-verify-20260303`

## Merge Bar Checklist (Explicit)

1. Real WS success proof exists (101 upgrade or explicit connected WS event in logs/trace).  
   **Status: FAIL**
   - Fresh real run trace: `/tmp/claude_tap_ws_strict_JH7Iuw/trace_20260303_171554.jsonl`
   - `transport=websocket`: 7 records
   - `response.status=101`: 0
   - `response.status=502`: 7
   - No explicit connected WS event to upstream found.

2. If no WS success, root cause is diagnosed with hard evidence.  
   **Status: PASS**
   - Proxy log shows repeated upstream connect timeout to `wss://chatgpt.com/backend-api/codex/responses`:
     `/tmp/claude_tap_ws_strict_JH7Iuw/trace_20260303_171554.log`
   - Codex stderr shows repeated `502 Bad Gateway` then fallback to HTTPS.
   - Network probe evidence in this environment:
     - `curl -I https://chatgpt.com/backend-api/codex/responses` returns `405` quickly (HTTPS path reachable via configured proxy).
     - `uv run python` probe with `aiohttp`:
       - `ClientSession(trust_env=True)` HTTP GET returns `405`, but `ws_connect(wss://chatgpt.com/backend-api/codex/responses)` times out.
       - `ClientSession(trust_env=False)` both HTTPS and WSS to `chatgpt.com` time out.
   - Environment variables confirm local outbound proxy dependency:
     - `HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:7897`
     - `all_proxy=socks5://127.0.0.1:7898`

3. PR claim is truthful to evidence (no over-claim).  
   **Status: PARTIAL / NEEDS SCOPE EDIT**
   - Code/test claims are supported (WS handler exists, unit tests pass).
   - Real WS success is not supported in this validation environment.
   - PR body currently says WS support is added (true at implementation level), but merge should avoid implying production WS path has been proven end-to-end.

## Re-read PR #22 Diff: Code-Level Reasons for WS 502/Fallback

Files reviewed:
- `claude_tap/proxy.py`
- `claude_tap/cli.py`
- `tests/test_ws_proxy.py`
- `tests/test_e2e.py`

Findings:
- WS 502 path is intentional in `_handle_websocket` when `session.ws_connect(...)` fails.
- Observed failures are `Connection timeout` before handshake completion to upstream `wss://chatgpt.com/...`, not a malformed local handshake response.
- No direct code bug in PR diff was found that explains these specific timeouts.
- Fallback behavior is from Codex client after repeated WS failures; proxy correctly records failures and then forwards successful HTTP fallback request.

## Reproduction Commands and Evidence

### A) Real forced-WS Codex run (targeted)
Command:
```bash
uv run python -m claude_tap \
  --tap-client codex \
  --tap-output-dir /tmp/claude_tap_ws_strict_JH7Iuw \
  --tap-no-update-check \
  -- --enable responses_websockets --enable responses_websockets_v2 \
  exec "Reply with exactly: WS_STRICT_OK"
```
Result:
- Codex attempts WS multiple times, receives 502 each time, then falls back to HTTPS and succeeds.
- Final output: `WS_STRICT_OK`

Evidence:
- Trace: `/tmp/claude_tap_ws_strict_JH7Iuw/trace_20260303_171554.jsonl`
- Log: `/tmp/claude_tap_ws_strict_JH7Iuw/trace_20260303_171554.log`
- HTML: `/tmp/claude_tap_ws_strict_JH7Iuw/trace_20260303_171554.html`

### B) Direct upstream reachability probes
Commands run:
```bash
curl -I --max-time 20 https://chatgpt.com/backend-api/codex/responses
```
```bash
uv run python - <<'PY'
import asyncio, aiohttp
# Probed GET and ws_connect with trust_env=False/True
PY
```
Observed:
- HTTPS endpoint responds (`405`) when proxy env is used.
- WSS connect times out in this environment, including with `trust_env=True`.

## Verified vs Not Verified

Verified:
- PR introduces WS proxy implementation and WS trace format.
- PR WS tests pass (`tests/test_ws_proxy.py`).
- Real run proves WS upgrade attempts reach proxy and are traced as websocket transport entries.
- Real run proves fallback to HTTPS still works and completes response.

Not Verified:
- Real upstream WS success (`101`) in this environment.
- Any trace evidence of connected upstream WS session with `ws_events` from live upstream.

## Recommendation

**Recommendation: block**

Decision logic under strict bar:
- Checklist item #1 (real WS success proof) failed.
- Therefore PR #22 does not meet the current merge bar yet.
- Keep implementation work, but do not merge until at least one real run shows WS success (`101` or explicit connected WS evidence).

Scope fallback (only if policy is relaxed):
- If reviewers intentionally relax the bar from “real WS proven” to “implementation + tests + fallback documented,” then this can be merged with explicit scope wording changes.

## Exact PR Wording/Summary Changes Suggested

Use wording like:
- “Adds WebSocket proxy support and trace capture for Codex WS Responses paths, validated by local WS integration tests.”
- “In this validation environment, upstream `wss://chatgpt.com/backend-api/codex/responses` timed out; real WS 101 was not observed. Codex fallback to HTTPS was observed and traced.”
- “Real upstream WS proof will be attached from a network environment that can establish WSS to ChatGPT Codex endpoint.”

Avoid wording like:
- “Real WS path validated end-to-end”
- “WS transport confirmed working in production upstream”

## Concrete Next Actions

1. Run one real validation in an environment where outbound WSS to `chatgpt.com` is confirmed (or with proxy known to support WSS CONNECT), then attach trace showing `transport=websocket` with `response.status=101`.
2. Update PR description to explicitly separate implemented capability from currently observed environment limitation.
3. Gate merge on chosen policy:
   - strict functional proof policy: block until step 1 completed.
   - implementation policy: merge-with-scope-change now, with follow-up issue for real WS upstream proof.
