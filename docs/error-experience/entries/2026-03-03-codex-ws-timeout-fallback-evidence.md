# Codex WS Timeout With Verified HTTPS Fallback (PR #22)

**Date:** 2026-03-03
**Tags:** codex, websocket, reverse-proxy, validation, fallback

## Context

While validating PR #22 (`feat/ws-proxy`) with real Codex runs, we needed hard evidence for WebSocket transport behavior and fallback behavior in the current environment.

## What Happened

- A normal Codex run through `claude_tap --tap-client codex` succeeded via HTTP/SSE (`POST /v1/responses`).
- Forced WS runs (`--enable responses_websockets` and `--enable responses_websockets_v2`) repeatedly failed to connect upstream:
  - `502 Bad Gateway`
  - timeout to `wss://chatgpt.com/backend-api/codex/responses`
- After retries, Codex automatically fell back to HTTPS transport and completed the turn successfully.

## Root Cause (Observed)

The observed failure is upstream WS connection timeout in this environment, not a local crash in the proxy process. The proxy records WS failure correctly, and the client fallback path recovers the run.

## Evidence

- `/tmp/pr22-ws-unblock-evidence-20260303/codex-ws-v1-run.log`
- `/tmp/pr22-ws-unblock-evidence-20260303/codex-ws-v2-run.log`
- `/tmp/pr22-ws-unblock-evidence-20260303/codex-ws-v1-run/trace_20260303_180901.jsonl`
- `/tmp/pr22-ws-unblock-evidence-20260303/codex-ws-v2-run/trace_20260303_180901.jsonl`

## Lesson

For transport-sensitive validation, separate three statements explicitly:
1. implemented behavior (code + tests),
2. observed behavior in this environment,
3. not-yet-verified behavior due to network/runtime constraints.

This prevents over-claiming while still allowing merge decisions on verified scope.
