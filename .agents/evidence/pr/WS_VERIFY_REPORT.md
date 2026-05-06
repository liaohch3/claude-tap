# PR #22 WebSocket Verification Report

Date: 2026-03-03
PR: https://github.com/liaohch3/claude-tap/pull/22
Branch: `feat/ws-proxy`
Workspace: `/private/tmp/claude-tap-pr22-ws-verify-20260303`
Evidence root: `/tmp/pr22-ws-unblock-evidence-20260303/`

## Scope Decision

This report applies the "truthful high-confidence" merge policy:
- Merge is acceptable if implementation claims are correct and verified boundaries are explicit.
- Merge is blocked only if the PR claims real upstream WS success that is not proven.

## Verification Matrix

1. Real E2E (Claude, pytest marker path)
   - Command: `uv run pytest tests/e2e/test_real_proxy.py::TestRealProxy::test_single_turn --run-real-e2e --timeout=180 -vv`
   - Result: PASS
   - Evidence: `/tmp/pr22-ws-unblock-evidence-20260303/pytest-real-proxy-single-turn.log`

2. Real E2E (Claude, multi-turn tmux conversation)
   - Command: `scripts/run_real_e2e_tmux.sh`
   - Result: PASS (full interactive multi-turn run completed)
   - Evidence:
     - `/tmp/pr22-ws-unblock-evidence-20260303/run_real_e2e_tmux.log`
     - `/tmp/pr22-ws-unblock-evidence-20260303/tmux-simple-1772532398.log`
     - `/tmp/pr22-ws-unblock-evidence-20260303/ctap-tmux-simple-1772532398/trace_20260303_180638.jsonl`

3. Real Codex reverse-proxy run (default transport)
   - Command: `uv run python -m claude_tap --tap-client codex ... -- exec "Reply with exactly: PR22_CODEX_PROXY_OK" ...`
   - Result: PASS (response completed through proxy)
   - Transport observed: HTTP/SSE (`POST /v1/responses`, no WS upgrade)
   - Evidence:
     - `/tmp/pr22-ws-unblock-evidence-20260303/codex-reverse-run.log`
     - `/tmp/pr22-ws-unblock-evidence-20260303/codex-reverse-run/trace_20260303_180811.jsonl`

4. Real Codex forced WebSocket attempts
   - Commands:
     - `--enable responses_websockets`
     - `--enable responses_websockets_v2`
   - Result: WS upstream connect repeatedly failed with `502 Bad Gateway` timeout to `wss://chatgpt.com/backend-api/codex/responses`; Codex then fell back to HTTPS and completed successfully.
   - Evidence:
     - `/tmp/pr22-ws-unblock-evidence-20260303/codex-ws-v1-run.log`
     - `/tmp/pr22-ws-unblock-evidence-20260303/codex-ws-v2-run.log`
     - `/tmp/pr22-ws-unblock-evidence-20260303/codex-ws-v1-run/trace_20260303_180901.jsonl`
     - `/tmp/pr22-ws-unblock-evidence-20260303/codex-ws-v2-run/trace_20260303_180901.jsonl`

5. Unit/integration WS proxy tests in PR scope
   - Command: `uv run pytest tests/test_ws_proxy.py -v`
   - Result: PASS
   - Coverage includes success relay (`101`), upstream WS failure (`502`), and HTTP+WS coexistence.

## What Is Implemented

- Reverse proxy WS handler is implemented in `claude_tap/proxy.py`.
- Root cause fixed: removed hardcoded `proxy=None` override from upstream `session.ws_connect(...)`, so WS now follows the same `ClientSession(trust_env=True)` proxy behavior as HTTP/SSE.
- WS relay and trace recording (`transport=websocket`, `method=WEBSOCKET`, `ws_events`) are implemented.
- Upstream WS connection failure path returns `502` and records an error.
- Codex launch path no longer forces `--disable responses_websockets`.

## What Is Verified

- WS implementation behavior is verified in automated tests (`tests/test_ws_proxy.py`).
- Regression guard added to assert upstream `ws_connect` is called without a `proxy=None` override.
- Real Codex runs through proxy are verified.
- Real forced WS attempts are verified to hit the WS path and then fall back to HTTPS on repeated upstream timeout.

## What Is Not Verified In This Environment

- A successful live upstream WS handshake (`101`) to `wss://chatgpt.com/backend-api/codex/responses`.
- A real trace containing live upstream `ws_events` from a successful Codex WS session.

## Residual Risk

- Primary residual risk is environment/network dependent WS reachability to ChatGPT Codex WSS endpoint.
- Functional risk in proxy implementation is reduced by passing WS tests plus real fallback observation.

## Merge Recommendation (Current Environment)

Recommendation: **MERGE WITH EXPLICIT CLAIM BOUNDARIES**.

Required PR wording constraints:
- State that WS proxy support is implemented and test-validated.
- State that in this environment, forced real WS upstream connection timed out and fallback-to-HTTPS was observed.
- Do not claim real upstream WS success was observed.
