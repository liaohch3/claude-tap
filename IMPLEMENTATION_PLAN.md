# Implementation Plan: Improved Diff Matching

Based on `specs/diff-matching.md`.

## Tasks

### R1: Message Prefix Matching (Primary Strategy)
- [x] **DONE** — `findPrevSameModel` and `findNextSameModel` already use prefix matching with model-based fallback.

### R2: Fallback Visual Indicator
- [x] **DONE** — `findPrevSameModel` now returns `{idx, isFallback}`. When `isFallback=true`, a ⚠️ amber banner shows in the diff modal with the text "No exact thread match found — showing closest same-model request". All 8 i18n locales updated.

### R3: Manual Diff Target Selection
- [x] **DONE** — Added a dropdown in the diff modal header listing all previous entries grouped by model. When user selects a different entry, the diff re-renders immediately. Added `_buildDiffTargetOptions()` helper and `showDiffForIdx(curIdx, triggerBtn, manualPrevIdx)` signature.

### R4: "Next" Navigation Consistency
- [x] **DONE** — `findNextSameModel` already uses prefix-matching logic identical to `findPrevSameModel`.

## Priority Order
1. R2 (simpler, adds warning indicator)
2. R3 (more complex, adds dropdown)

## Acceptance Criteria
1. Prefix-matched diffs show normally (no indicator)
2. Fallback diffs show a visible warning indicator
3. A dropdown allows manual selection of diff target
4. All 22 existing tests pass

ALL TASKS COMPLETE
