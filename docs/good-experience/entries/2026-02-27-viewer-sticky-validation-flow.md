# UI Fix Delivery Pattern: Sticky Header + Evidence-First Validation

**Date:** 2026-02-27
**Tags:** ui, viewer, testing, workflow, pr

## Context

A small UI behavior fix (sticky action controls in the viewer detail panel) needed to be delivered with reliable evidence for review.

## What Worked

1. Applied a minimal CSS-only fix first, without changing JS behavior.
2. Ran fast quality gates early (`ruff` + `pytest`) to quickly detect regressions.
3. Produced visual evidence (before/after scrolling state) for PR review.
4. Added explicit process requirements in `AGENTS.md` so future UI changes consistently include E2E validation and screenshots.

## Why This Pattern Is Good

- Low risk: tiny change surface.
- High review clarity: behavior proof is visible in screenshots.
- Reusable process: same flow can be applied to future UI changes.

## Lesson Learned

For UI behavior fixes, evidence is part of the deliverable. A good PR is not only "code + tests" but also "visual proof + explicit validation steps".
