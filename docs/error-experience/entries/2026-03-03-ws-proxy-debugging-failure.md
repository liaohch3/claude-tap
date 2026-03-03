# WS Proxy Debugging Failure — Agent Blind Spot

**Date:** 2026-03-03
**Severity:** High (hours wasted, multiple agent iterations, cron loops)
**PR:** #22 (WebSocket proxy support)

## What Happened

PR #22 added WebSocket proxy support. After implementation, real Codex WS
connections kept timing out. Multiple Codex agents were spawned across many
iterations — a cron monitoring job ran continuously, spawning more agents,
checking status, retrying — but **none** identified the root cause.

The human user stepped in, asked to check the WS connect code path, and
within minutes pinpointed the exact problem.

## Root Cause (of the Bug)

`claude_tap/proxy.py` line 379 had `proxy=None` hardcoded in
`session.ws_connect()`. This explicitly bypassed the Clash Verge proxy
(`http://127.0.0.1:7897`) that all HTTP requests used successfully.

Direct WSS connections to `wss://chatgpt.com` timed out because the machine
required a proxy for external access.

## Root Cause (of the Debugging Failure)

### 1. Agents never read the actual code path

The `ws_connect()` call had `proxy=None` in plain sight. Any agent reading
that single line should have asked: "Why is proxy disabled for WS when HTTP
requests work fine through the proxy?"

### 2. Over-reliance on black-box testing

Agents kept re-running Codex with different flags (`--enable responses_websockets`,
`--enable responses_websockets_v2`) and analyzing timeout logs. None traced
the code path from the WS upgrade handler → upstream connect → `ws_connect()`
call parameters.

### 3. Cron monitoring loops burned cycles without progress

A monitoring cron job kept cycling: check status → agent still stuck →
spawn more agents → repeat. Many iterations, zero conceptual progress.
The loop was busy, not productive.

### 4. No systematic debugging methodology

A simple comparison would have solved it in minutes:
- **Does HTTP work?** → Yes (through proxy)
- **Does WS work?** → No (timeout)
- **What's different in the code path?** → HTTP uses session default proxy,
  WS has explicit `proxy=None`
- **That's the bug.**

## The Fix

One line: remove `proxy=None` from `session.ws_connect()`. Let
`trust_env=True` on the `ClientSession` handle proxy resolution naturally.

Commit: `fcb2982`

## Lessons Learned

1. **Read the code before running tests.** When debugging proxy issues,
   trace the actual network call and check what proxy settings are passed.

2. **Compare working vs non-working paths.** HTTP worked, WS didn't. The
   diff between those two code paths is exactly where the bug lives.

3. **Stop the loop early.** If 2-3 iterations of black-box testing haven't
   found the issue, switch strategy: read code, add logging, trace the call.

4. **Simple mental models beat brute force.** "Proxy works for A but not B
   → B must handle proxy differently" is a 30-second insight. No amount of
   re-running tests with different flags will discover a hardcoded `proxy=None`.

5. **Cron loops are not debugging.** Automated retry loops are for monitoring
   known-good processes, not for solving unknown bugs. When stuck, stop the
   loop and think.

## Standards (Added)

See `docs/standards/debugging-standards.md` for the full checklist.

Key additions:
- Before spawning agents for debugging: spend 5 minutes reading the relevant
  code path yourself
- When proxy/network issues: always check what `proxy=` parameter is passed
  in the actual connect call
- After 2 failed iterations of the same approach: STOP and change approach
- Document "working vs broken" comparison explicitly before diving into fixes
