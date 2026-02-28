# TODO: Support Codex WebSocket transport for /v1/responses

**Date:** 2026-02-28
**Priority:** Medium
**Status:** Planned

## Context

Codex CLI v0.106.0+ defaults to WebSocket transport for `/v1/responses` API calls
(`responses_websockets` and `responses_websockets_v2` features). Currently claude-tap
works around this by auto-injecting `--disable responses_websockets` to force HTTP,
which allows the existing HTTP reverse proxy to capture all requests.

This is a functional workaround but not ideal — WebSocket transport is likely faster
and will become the default/only path in future Codex versions.

## Goal

Natively support WebSocket interception in claude-tap's reverse proxy mode, so Codex
can use its default WebSocket transport while claude-tap still captures all API calls.

## Approach Options

### Option A: WebSocket MITM proxy
- Intercept WebSocket upgrade requests to `/v1/responses`
- Proxy the WebSocket connection, recording all frames
- Reassemble frames into the same trace format (request body + SSE-equivalent events)
- Pros: Transparent to Codex, no CLI flag injection needed
- Cons: More complex, need to handle WS frame reassembly

### Option B: Forward proxy with CONNECT tunneling
- Use forward proxy mode (HTTP_PROXY/HTTPS_PROXY) with TLS interception
- Intercept both HTTP and WebSocket traffic at the TLS layer
- Pros: Works for all transport types
- Cons: Requires TLS cert injection, more moving parts

### Option C: Hybrid — detect and adapt
- Detect if Codex is using WebSocket (check for Upgrade headers)
- If WebSocket: proxy the WS connection and record frames
- If HTTP: use existing streaming proxy path
- Pros: Backward compatible, works with any Codex version
- Cons: Two code paths to maintain

## Implementation Notes

- WebSocket frames for Responses API likely follow the same SSE-like event structure
- Need to investigate the exact WebSocket message format Codex uses
- `aiohttp` (already a dependency) supports WebSocket proxying
- The `--disable` flag workaround should remain as a fallback option

## References

- Fix commit: `a0e00e2` (disable websocket transport in reverse mode)
- Error experience: `docs/error-experience/entries/2026-02-28-codex-reverse-websocket-capture-gap.md`
- Codex CLI flags: `--enable/--disable responses_websockets[_v2]`
