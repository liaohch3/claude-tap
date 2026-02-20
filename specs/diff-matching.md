# Spec: Improved Diff Matching in Viewer

## Problem

The viewer's "Diff with Prev" feature compares consecutive API requests. When Claude Code spawns parallel subagents, requests from different conversation threads interleave. Matching by model alone pairs unrelated requests, producing meaningless diffs.

Current state: `findPrevSameModel` already uses message prefix matching as primary strategy, with model-based fallback. The prefix matching works correctly. The fallback still produces wrong diffs for unrelated subagent requests.

## Requirements

### R1: Fallback Visual Indicator (HIGH PRIORITY)
When diff falls back to model-based matching (no prefix match found):
- Show a visible warning banner/badge in the diff modal header area
- Use amber/orange color (consistent with existing `--amber` CSS variable)
- Text: "⚠️ Approximate match — no shared message history" (and i18n equivalents)
- When prefix-matched: show NO indicator (clean, normal diff)
- Implementation: `findPrevSameModel` must return both the index AND whether it was a prefix match or fallback. Use an object return `{idx, method}` or add a separate function.

### R2: Manual Diff Target Selector (HIGH PRIORITY)
- Add a `<select>` dropdown next to the diff modal title (between the nav arrows and close button)
- Populate with ALL previous requests (not just same-model), showing: Turn number + model name + message count
- Group options by model using `<optgroup>`
- The auto-selected entry should have "(auto)" suffix
- Changing the dropdown re-renders the diff immediately with the new target
- This lets users manually compare ANY two requests

### R3: All Existing Tests Must Pass
- `python3 -m pytest tests/ -v` — all 22 tests must pass
- No changes to Python files

### R4: i18n Support
- All new user-visible strings must go through the existing `t()` translation function
- Add entries to ALL language objects (en, zh, ja, ko, fr, ar, de, ru)
- Key names: `approx_match`, `select_diff_target`, `auto_suffix`

## Files to Modify
- `claude_tap/viewer.html` ONLY

## Acceptance Criteria
1. Prefix-matched diffs: clean, no warning
2. Fallback diffs: amber warning banner visible
3. Dropdown allows selecting any previous request as diff target
4. i18n strings added for all 8 languages
5. All 22 tests pass
6. HTML regenerated from real traces works in browser

## When Done
Add "ALL TASKS COMPLETE" to the bottom of IMPLEMENTATION_PLAN.md
