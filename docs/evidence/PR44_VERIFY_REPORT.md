# PR #44 Verification Report

Date: 2026-03-17
PR: https://github.com/liaohch3/claude-tap/pull/44
Branch: `fix/codex-responses-40-41`
Workspace: `/tmp/claude-tap-issue-40-41`
Evidence root: `/tmp/pr44-codex-exec-1773742623`

## Scope

This PR fixes viewer and parser support for OpenAI Codex Responses traces:
- `#40` Empty thinking block, zero token counts, null response body
- `#41` User messages missing in HTML/JSONL viewer

The verification target is the real Codex OAuth path through `https://chatgpt.com/backend-api/codex`, because that is the affected trace format.

## Verification Matrix

1. Targeted tests
   - Command: `uv run python -m pytest tests/test_responses_support.py tests/test_responses_browser.py -q`
   - Result: PASS (`5 passed`)

2. Full repository tests in PR scope
   - Command: `uv run python -m pytest tests/ -x --timeout=60 -q`
   - Result: PASS (`103 passed, 25 skipped`)

3. Lint / format gates
   - Commands:
     - `uv run ruff check .`
     - `uv run ruff format --check .`
   - Result: PASS

4. Real Codex multi-turn E2E through claude-tap
   - Turn 1 command:
     - `uv run python -m claude_tap --tap-client codex --tap-target https://chatgpt.com/backend-api/codex --tap-output-dir /tmp/pr44-codex-exec-1773742623 --tap-no-open --tap-no-update-check -- exec 'Reply with exactly: PR44_TURN1_OK' --full-auto --skip-git-repo-check --json -o /tmp/pr44-codex-exec-1773742623/turn1.jsonl`
   - Turn 1 result: PASS
   - Expected: assistant replies exactly `PR44_TURN1_OK`
   - Observed: Codex reply text was `PR44_TURN1_OK`
   - Trace: `/tmp/pr44-codex-exec-1773742623/trace_20260317_181704.jsonl`
   - Viewer: `/tmp/pr44-codex-exec-1773742623/trace_20260317_181704.html`

5. Real Codex conversation continuity E2E
   - Turn 2 command:
     - `uv run python -m claude_tap --tap-client codex --tap-target https://chatgpt.com/backend-api/codex --tap-output-dir /tmp/pr44-codex-exec-1773742623 --tap-no-open --tap-no-update-check -- exec resume --last 'What was the exact text I asked you to reply with in the previous turn?' --full-auto --skip-git-repo-check --json -o /tmp/pr44-codex-exec-1773742623/turn2.jsonl`
   - Turn 2 result: PASS
   - Expected: assistant recalls prior turn text
   - Observed: Codex reply text was `` `PR44_TURN1_OK` ``
   - Trace: `/tmp/pr44-codex-exec-1773742623/trace_20260317_181715.jsonl`
   - Viewer: `/tmp/pr44-codex-exec-1773742623/trace_20260317_181715.html`

6. Viewer evidence screenshots
   - Desktop screenshots rendered from the real HTML traces with viewport `1440x1600`
   - Screenshot check: `python3 scripts/check_screenshots.py docs/evidence/pr44`
   - Viewer render check: `python3 scripts/verify_screenshots.py /tmp/pr44-codex-exec-1773742623/trace_*.html`
   - Result: PASS
   - Files:
     - `docs/evidence/pr44/pr44-turn1-viewer.png`
     - `docs/evidence/pr44/pr44-turn2-viewer.png`

## What Was Verified

- The real Codex OAuth trace format is captured by `claude-tap` as a Responses trace.
- The generated viewer shows the user message from `request.body.input`.
- The generated viewer shows the assistant response text.
- Token usage is extracted and displayed from the Responses event stream.
- A second-turn `resume --last` run preserves conversation continuity and the viewer still renders correctly.
- The empty thinking block regression is covered by browser tests and did not appear in the real screenshots for these zero-reasoning runs.

## Residual Risk

- The real E2E run here exercised the Codex OAuth HTTP/SSE path. It did not prove a live upstream WebSocket Responses session.
- The screenshots validate two real traces, but not every possible Responses item variant.

## Merge Recommendation

Recommendation: **MERGE**.

The PR now meets the repository bar for this change class:
- code/tests/lint pass
- real multi-turn Codex E2E passed
- viewer screenshots from real traces are attached in-repo
