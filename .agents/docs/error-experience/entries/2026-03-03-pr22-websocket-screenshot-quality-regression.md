# PR #22 Screenshot Quality Regression (WebSocket Proxy Fix)

**Date:** 2026-03-03
**Tags:** pr, screenshot, viewport, encoding, websocket, review

## What Happened

Timeline:

1. PR #22 (WebSocket proxy fix) required screenshot evidence for the trace viewer output.
2. Screenshots were captured in an openclaw browser session that defaulted to a narrow viewport (~750px width).
3. The rendered trace log contained raw Unicode arrows (`→`, `←`) and those characters displayed as garbled glyphs in the screenshot.
4. The captured screenshot focused on a `GET` request row instead of the `101 WEBSOCKET` row that was the core proof for the fix.
5. The screenshots were committed and pushed without a dedicated screenshot quality review step.

Specific failures:

- Desktop UI evidence looked like a mobile layout.
- Important log symbols were corrupted.
- The screenshot did not show the feature being validated.
- No pre-commit evidence verification caught the mismatch.

## Root Causes

1. **Viewport control gap:** no enforced minimum viewport width for desktop evidence screenshots.
2. **Encoding safety gap:** generated HTML used raw Unicode arrows in log rendering instead of ASCII-safe symbols or entities.
3. **Content verification gap:** no explicit check that screenshot content matched the exact PR claim (`101 WEBSOCKET` for this fix).
4. **Process gap:** no mandatory screenshot checklist before commit/push.

## Fix Applied

1. Defined screenshot standards with hard desktop viewport minimum (`>=1280px`).
2. Added encoding rule to avoid raw Unicode arrows in generated HTML evidence content.
3. Added content verification requirements to confirm screenshots display the specific feature/fix target.
4. Added automated validation script (`scripts/check_screenshots.py`) for baseline image quality checks.
5. Wired screenshot validation into CI for PR evidence images.

## Lessons Learned

1. Screenshot evidence needs the same validation rigor as tests and linting.
2. Visual proof must be scoped to the exact behavior under review, not adjacent activity.
3. Encoding decisions in generated artifacts can silently degrade review quality.
4. Lightweight automated checks plus a short human checklist prevent most screenshot regressions.
