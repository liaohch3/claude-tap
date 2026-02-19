# Implementation Plan: Mobile Responsive Viewer

Spec: `specs/mobile-responsive.md`
Target file: `claude_tap/viewer.html`

## Tasks

- [x] **R1**: Responsive stacked layout — sidebar collapsible/toggleable; detail full-width on mobile; "← Back" button to return to sidebar; CSS `@media (max-width: 768px)`
- [x] **R2**: Header & stats bar — token stats wrap to 2 rows or compact grid; path-filter chips horizontally scrollable; top bar stacks vertically; file paths truncate with ellipsis
- [x] **R3**: Action buttons — stack vertically on mobile; touch targets ≥ 44px (min-height: 44px)
- [x] **R4**: Detail panel — no horizontal overflow; code blocks `overflow-x: auto`; long text word-wraps
- [x] **R5**: Diff modal — full-screen on mobile; side-by-side diff switches to block layout; dropdown full-width; nav/close buttons ≥ 44px touch target
- [x] **R6**: Search bar — inherits full width from sidebar; larger font-size on mobile for readability

## Constraints
- All changes in `claude_tap/viewer.html` only
- Prefer CSS-only `@media` solutions
- Do NOT break desktop layout
- All 22 tests must pass

ALL TASKS COMPLETE
