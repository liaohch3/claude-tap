# PROMPT — Build Mode

You are working on the `claude-tap` project.

## Your Task

1. Read `specs/keyboard-nav-order.md` for the full specification
2. Read `IMPLEMENTATION_PLAN.md` if it exists — pick the highest-priority incomplete task
3. If no plan exists, create `IMPLEMENTATION_PLAN.md` first, then pick a task
4. Implement in `claude_tap/viewer.html`
5. Run tests: `python3 -m pytest tests/ -v` — all must pass
6. Update `IMPLEMENTATION_PLAN.md`, commit: `git add -A && git commit -m "..."`
7. When ALL tasks done, add "ALL TASKS COMPLETE" at bottom of IMPLEMENTATION_PLAN.md

## Key Context

- Sidebar renders entries grouped by model via `renderSidebar()` — groups sorted by `modelPriority()`
- `filtered` array is chronological order
- Each `.sidebar-item` has `data-idx` pointing to its index in `filtered`
- `activeIdx` is the current index in `filtered`
- Keyboard handler: `document.addEventListener('keydown', ...)` near bottom of file
- Mobile nav: `mobilePrev()` / `mobileNext()` functions

## Constraints
- `claude_tap/viewer.html` ONLY
- All 22 tests must pass
- Don't break desktop or mobile layout
