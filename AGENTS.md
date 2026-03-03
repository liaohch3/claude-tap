# AGENTS Index

This file is the entry point for contributor rules. Detailed policy text lives in `docs/standards/*.md`.

## Non-negotiable Rules

These are mandatory and enforced in review:

1. Run gate checks before every commit: `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest tests/ -x --timeout=60`.
2. UI changes require PR screenshots with `raw.githubusercontent.com` absolute URLs.
3. One concern per commit (no mixed refactor + feature/fix in the same commit).
4. English only for code/comments/docs/commits, except `README_zh.md`.
5. Evidence must use real trace data from `.traces/` (no synthetic mock screenshots/demos).
6. Run pre-work checklist before coding and pre-PR checklist before opening a PR.
7. Do not leave local-only work; you must `git add`, `git commit`, and `git push`.
8. You must open a GitHub PR with `gh pr create`.

## Standards Catalog

- Hard rules and repository policies: `docs/standards/hard-rules.md`
- Validation gates and required commands: `docs/standards/validation-and-gates.md`
- E2E and screenshot evidence requirements: `docs/standards/e2e-and-evidence.md`
- Screenshot capture and validation standards: `docs/standards/screenshot-standards.md`
- Coding and runtime safety rules: `docs/standards/coding-and-runtime.md`
- Workflow, review, and Brain/Hands protocol: `docs/standards/workflow-and-review.md`
- Debugging methodology and anti-patterns: `docs/standards/debugging-standards.md`
- Metadata and maintenance process for standards docs: `docs/standards/README.md`

## Legibility Checks

Deterministic legibility checks are implemented in `scripts/check_legibility.py` and run in CI via `.github/workflows/legibility.yml`.
