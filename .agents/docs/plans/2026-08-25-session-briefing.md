---
status: active
---

# Session briefing from existing metadata

Date: 2026-08-25

## Problem

The viewer already explains one turn: cost, cache-miss cause, tool-result
size, and user-input provenance. Opening a 200-turn session still makes the
reader assemble those signals by hand.

## Approach

Compute one session-level briefing in Python from records that already exist.
The viewer banner and `claude-tap summary` emit the same JSON. No model API.

Three optional lines:

1. Cost, and the share of spend that starts at the first cold cache write
   after a hit.
2. That cache-break turn, with a named cause only when system XOR tools
   changed against the previous turn.
3. The three largest request-side tool results over 10 KB.

## Out of scope

Pretty charts, LLM prose, new `--tap-client` entries, live file-drop
recompute, and a full port of the viewer cache-diagnosis state machine.
