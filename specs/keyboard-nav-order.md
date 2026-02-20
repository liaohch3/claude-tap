# Spec: Fix Keyboard Navigation Order

## Problem

Keyboard navigation (j/k, ↑/↓) follows `filtered` array order (chronological), but the sidebar displays entries **grouped by model** (e.g., all Opus first, then all Haiku). Users expect navigation to match the visual order they see in the sidebar.

The mobile ← → nav buttons have the same issue.

## Requirements

### R1: Navigation Must Follow Visual Order
- Build a `visualOrder` array that matches the DOM order of `.sidebar-item` elements
- j/↓ moves to the next item in visual order
- k/↑ moves to the previous item in visual order
- Mobile prev/next buttons use the same visual order
- When only one model group exists (no grouping), visual order = filtered order (no change)

### R2: Collapsed Groups
- If a model group is collapsed, skip its entries during navigation (or expand the group when navigating into it — either approach is fine, pick the simpler one)

### R3: No Other Behavior Changes
- Clicking a sidebar item still works as before
- Diff matching logic unchanged
- Desktop layout unchanged

## Constraints
- All changes in `claude_tap/viewer.html` ONLY
- All 22 tests must pass
- Keep it simple

## When Done
Add "ALL TASKS COMPLETE" to the bottom of IMPLEMENTATION_PLAN.md
