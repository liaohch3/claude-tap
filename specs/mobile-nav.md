# Spec: Mobile Detail Navigation

## Problem

On mobile, selecting an entry hides the sidebar list and shows the detail view full-width. Users cannot browse through requests without going back to the list each time.

## Requirements

### R1: Previous/Next Navigation in Detail View
- Add navigation arrows (← →) or (▲ ▼) at the top of the detail view on mobile
- Buttons should navigate to the previous/next entry in the `filtered` array
- Show current position: "3 / 18" (current index / total)
- Buttons disable at boundaries (first/last entry)
- Only visible on mobile (≤768px) — desktop has the sidebar always visible

### R2: Swipe Gesture (Optional Enhancement)
- If feasible with minimal JS: support left/right swipe on the detail area to navigate prev/next
- This is nice-to-have, not required

### R3: Keyboard Navigation Still Works
- Existing `j`/`k` or arrow key navigation should still work on mobile if a hardware keyboard is connected
- The mobile nav buttons are a touch-friendly supplement, not a replacement

## Constraints
- All changes in `claude_tap/viewer.html` ONLY
- Do NOT break desktop layout
- All 22 tests must pass: `python3 -m pytest tests/ -v`
- Keep it simple — a small sticky bar at the top of detail view is fine

## When Done
Add "ALL TASKS COMPLETE" to the bottom of IMPLEMENTATION_PLAN.md
