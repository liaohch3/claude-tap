---
owner: claude-tap-maintainers
last_reviewed: 2026-03-03
source_of_truth: AGENTS.md
---

# E2E Validation Requirements

If a change affects proxying, trace capture, CLI session flow, auth handling, or other end-to-end behavior, run real E2E validation before opening a PR.

Preferred commands:

```bash
uv run pytest tests/e2e/ --run-real-e2e --timeout=300
uv run pytest tests/e2e/test_real_proxy.py::TestRealProxy::test_single_turn --run-real-e2e --timeout=180
```

Manual alternatives:

```bash
scripts/run_real_e2e.sh
scripts/run_real_e2e_tmux.sh
```

If real E2E cannot run (for example, missing auth/token), document reason and residual risk in the PR body.

# E2E Conversation Rule

Each E2E run must include at least one complete multi-turn conversation.
For conversation validation and screenshot evidence, use tmux interactive flow (`scripts/run_real_e2e_tmux.sh`).
Do not use `claude -p` one-shot runs as proof of conversation completeness.

# UI Evidence Requirements

For PRs changing UI layout, styles, interaction flow, or rendered content:

- Include at least one screenshot per changed screen/state.
- Include before/after screenshots when a visual diff matters.
- Include mobile screenshots when mobile behavior is affected.
- Use real trace artifacts from `.traces/trace_*.jsonl` or real run outputs.
- For E2E-related UI changes, screenshots must come from a run that completed at least one full multi-turn conversation.

# Screenshot Quality Gate

Every screenshot committed as PR evidence must pass these checks before `git add`:

## Mandatory Checks
1. **Viewport width ≥ 1280px** — Headless browsers often default to narrow viewports. Always resize to desktop dimensions (1280x800 or 1440x900) before capture.
2. **Content matches claim** — If the PR says "WS trace captured", the screenshot must visibly show the WS trace, not a different request or a loading screen.
3. **No encoding corruption** — Unicode arrows (→←), CJK characters, and emoji must render correctly. If in doubt, use ASCII equivalents or HTML entities in generated evidence pages.
4. **No error pages** — 404, ERR_EMPTY_RESPONSE, blank pages, or "page not found" are not evidence.
5. **Minimum resolution** — Image width must be ≥ 1000px. Anything narrower is likely a mobile/tablet capture.
6. **File size sanity** — Screenshots < 10KB are likely blank or error pages. Typical trace viewer screenshots are 100KB–500KB.

## Best Practices
- For log/text evidence, render as styled HTML (dark card, monospace, syntax highlighting) rather than serving raw `.log` files — avoids font/encoding issues.
- When taking trace viewer screenshots, navigate to the specific entry first, then capture.
- Use `scripts/check_screenshots.sh` to automate pre-commit validation.

## Anti-Pattern: Blind Commit
Never `git add` + `git commit` + `git push` screenshots without opening and reviewing them first. This wastes reviewer time and erodes trust in the evidence.
