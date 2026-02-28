# Codex Reverse Mode Could Miss Responses Traffic

**Date:** 2026-02-28  
**Severity:** High  
**Tags:** codex, reverse-proxy, websocket, trace-capture

## Problem

In `--tap-client codex` reverse mode, some runs only captured `/v1/models` and showed
zero token usage in the viewer. Follow-up conversation traffic was not consistently
captured as `/v1/responses`.

## Root Cause

Codex can attempt websocket-based Responses paths in interactive/session flows.
When websocket behavior is enabled via user config or feature toggles, reverse-mode
base URL routing can become inconsistent for trace capture.

## Fix

- In reverse mode for Codex, auto-inject:
  - `--disable responses_websockets`
  - `--disable responses_websockets_v2`
- Preserve user intent: if a feature is explicitly overridden via `--enable`,
  `--disable`, or `-c/--config features.<name>=...`, do not force override it.

## Validation

- Added E2E assertions that reverse-mode launch includes websocket-disable flags.
- Added E2E assertions that explicit user feature override is respected.
- Ran full gate checks (`ruff`, format check, `pytest tests/ -x --timeout=60`).

## Lesson Learned

For proxy-capture reliability, do not assume Codex transport is always HTTP POST.
When reverse proxying, explicitly pin transport features to the capture path and
make override behavior deterministic in tests.
