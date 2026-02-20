# Implementation Plan: Fix Keyboard Navigation Order

Spec: `specs/keyboard-nav-order.md`

## Tasks

- [ ] **T1**: Build `visualOrder` array in `renderSidebar()` that reflects DOM order of visible `.sidebar-item` elements; update keyboard nav (j/k/↑/↓) and mobile prev/next to follow visual order; skip collapsed group entries; rebuild after group toggle.

## Notes

- All changes in `claude_tap/viewer.html` only
- `visualOrder` is an array of `filtered` indices in DOM/visual order, excluding items inside collapsed groups
- `buildVisualOrder()` reads `.sidebar-item` elements from DOM and filters by parent visibility
- `visualNavigate(delta)` looks up current position in `visualOrder` and steps by `delta`
- `updateMobileNav()` uses `visualOrder.length` and visual position for prev/next disabled state
