---
owner: claude-tap-maintainers
last_reviewed: 2026-03-03
source_of_truth: AGENTS.md
---

# Screenshot Standards

These rules apply to PR evidence screenshots used to validate UI, viewer output, trace logs, and proxy behavior.

## Viewport Rules

1. Desktop screenshots must use a viewport width of at least `1280px`.
2. If a change is desktop-only, do not submit mobile-layout screenshots as primary evidence.
3. If a change intentionally affects mobile behavior, include separate mobile screenshots and label them clearly.

## Encoding Rules

1. Use ASCII-safe characters or HTML entities in generated HTML/log views for symbols that may render inconsistently.
2. Do not use raw Unicode arrows (`→`, `←`) in generated HTML evidence content.
3. Prefer explicit text alternatives (`->`, `<-`) or entities (`&rarr;`, `&larr;`) to avoid garbled output.

## Content Verification Rules

1. Every screenshot must show the exact feature or fix claimed in the PR description.
2. For protocol fixes, capture the specific row/event proving the behavior (example: `101 WEBSOCKET`, not unrelated `GET` rows).
3. Before commit, verify the screenshot path and filename match the content shown in the PR markdown links.

## Screenshot Pre-commit Checklist

Run this checklist before `git commit` when screenshots are part of the PR:

1. Viewport width is `>=1280px` for desktop evidence.
2. Screenshot contains the exact target state/line proving the fix.
3. No garbled characters or encoding corruption in visible text.
4. Images are legible, not mostly blank, and reasonably sized.
5. Local automated check passes:

```bash
python3 scripts/check_screenshots.py docs/evidence/
```
