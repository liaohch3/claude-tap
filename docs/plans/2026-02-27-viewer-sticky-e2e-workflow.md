# Viewer Sticky Action Bar + Validation Workflow Plan

## Problem

The action button row in `claude_tap/viewer.html` disappeared when users scrolled down in the detail pane.
This reduced operational efficiency for repeated actions (`Request JSON`, `cURL`, `Diff with Prev`).

## Scope

- Keep action bar visible while scrolling detail content.
- Update repository workflow guidance so future PRs include:
  - real E2E validation expectations
  - UI screenshot requirements for UI-affecting changes
- Produce review evidence (test outputs + screenshots).

## Execution Order

1. Define target behavior and scope.
2. Implement minimal UI change.
3. Run focused tests for impacted behavior.
4. Run E2E/browser validation and collect screenshots.
5. Run full project quality gates.
6. Prepare PR with evidence.
7. Record lessons learned.

## Change Summary

- `claude_tap/viewer.html`
  - make `.action-bar` sticky in the detail scroll container.
- `AGENTS.md`
  - add `E2E Validation Requirements` section.
  - add `PR Requirements for UI Changes` section.

## Validation

- Unit/integration tests: `uv run pytest tests/ -x --timeout=60`
- Lint/format checks:
  - `uv run ruff check .`
  - `uv run ruff format --check .`
- UI evidence:
  - top-state screenshot
  - scrolled-state screenshot confirming sticky action bar remains visible

## Out of Scope

- Feature redesign in viewer layout.
- Non-related dependency updates.
