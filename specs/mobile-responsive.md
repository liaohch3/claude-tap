# Spec: Mobile Responsive Viewer

## Problem

The viewer HTML is designed for desktop only. On mobile (width < 768px):
- Sidebar and detail panel are side-by-side, both squeezed
- Detail panel content overflows horizontally
- Diff modal is too wide, content cut off on right
- Header buttons ("Request JSON", "cURL", "Diff with Prev") overflow
- Token stats bar wraps awkwardly
- Code blocks in messages overflow without scroll
- Diff side-by-side comparison (OLD vs NEW) is unreadable on narrow screens

## Requirements

### R1: Responsive Layout (< 768px)
- Switch from side-by-side (sidebar + detail) to **stacked layout**
- Sidebar becomes a collapsible/toggleable panel at the top or a slide-out drawer
- When an entry is selected, show detail full-width; provide a "← Back to list" button to return to sidebar
- Use CSS `@media (max-width: 768px)` — no JS layout changes needed for basic responsiveness

### R2: Header & Stats Bar
- Token stats: wrap into 2 rows or use a compact grid on mobile
- Path filter tabs: horizontal scroll if needed
- Top bar (logo + stats): stack vertically or make scrollable
- File paths (JSONL/HTML): truncate with ellipsis on mobile

### R3: Action Buttons
- "Request JSON", "cURL", "Diff with Prev" buttons: stack vertically or use icon-only mode on mobile
- Ensure touch targets are at least 44px tall (Apple HIG)

### R4: Detail Panel
- System prompt, messages, tools: full width, no horizontal overflow
- Code blocks: `overflow-x: auto` with scroll
- Long text: word-wrap properly

### R5: Diff Modal
- On mobile, diff modal should be **full-screen** (not a centered floating modal)
- Side-by-side diff (OLD vs NEW): switch to **stacked vertical** layout on mobile
- "Compare against" dropdown: full-width on mobile
- Nav arrows and close button: large enough for touch

### R6: Search
- Search bar: full width on mobile
- Search results: readable without horizontal scroll

## Constraints
- All changes in `claude_tap/viewer.html` ONLY (CSS + minimal JS)
- Prefer CSS-only solutions (@media queries)
- Do NOT break desktop layout
- All 22 existing tests must pass
- Keep the viewer self-contained (no external CSS/JS)

## Verification
Test with these viewports:
- iPhone 14 Pro: 393x852
- iPhone SE: 375x667
- iPad Mini: 768x1024
- Desktop: 1400x900

Generate HTML and verify:
```bash
python3 -c "
from claude_tap.viewer import _generate_html_viewer
from pathlib import Path
_generate_html_viewer(Path('.traces/trace_20260218_083822.jsonl'), Path('/tmp/mobile-test.html'))
"
```

## Acceptance Criteria
1. On mobile: sidebar toggleable, detail full-width
2. Diff modal: full-screen on mobile, stacked (not side-by-side) OLD/NEW
3. No horizontal overflow on any element
4. Touch targets >= 44px
5. Desktop layout unchanged
6. All 22 tests pass

## When Done
Add "ALL TASKS COMPLETE" to the bottom of IMPLEMENTATION_PLAN.md
