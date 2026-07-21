# Pi SSE requests bypassed useful trace capture

Date: 2026-07-21

## What broke

Pi 0.81 used a cached WebSocket for its default `auto` transport. The model reply completed, but the dashboard kept showing an unknown model, zero tokens, and no user prompt until the idle WebSocket closed five minutes later.

Switching Pi to SSE did not solve the problem by itself. Node fetch needed environment proxy support enabled, and once the request reached the forward proxy its zstd-compressed body was stored as replacement-character text instead of parsed JSON.

## Root cause

The Pi SSE request combined two behaviors that the existing forward path did not cover:

1. Node fetch only used the injected `HTTPS_PROXY` after `NODE_USE_ENV_PROXY=1` was enabled.
2. Pi compressed the Responses API request body with `Content-Encoding: zstd`, while the forward proxy passed the raw compressed bytes directly to `_parse_request_body_for_trace()`.

Because capture-only detection received a string instead of a request object, it also failed to recognize the model request and contacted upstream despite capture-only mode being active.

## What fixed it

- Enable Node environment proxy support when claude-tap launches Pi in forward mode.
- Decompress zstd request bodies for trace parsing while preserving the original compressed bytes and headers for upstream forwarding.
- Add a capture-only regression test that fails if the compressed Pi request reaches upstream or if the stored body is not the original JSON object.

## Verification

A real two-turn Pi tmux session remained open while both completed SSE requests were already present in SQLite. Each record contained the correct `gpt-5.6-luna` model, user prompt, completed response, and 14 stored SSE events.

## Lessons

1. Test the actual client wire format, not only an equivalent uncompressed JSON request.
2. Capture-only tests must assert that upstream was not contacted; checking the exported trace alone can miss a leak.
3. For long-lived transports, verify persistence while the client connection is still alive. A trace that appears after process exit does not prove live observability.
4. Transport changes can expose separate proxy-routing and content-decoding requirements, so validate both independently.
