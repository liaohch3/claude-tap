---
owner: claude-tap-maintainers
last_reviewed: 2026-03-03
source_of_truth: AGENTS.md
---

# Hard Rules

These rules are mandatory. If you cannot comply, stop and explain why.

1. Gate checks before every commit: `ruff check`, `ruff format --check`, `pytest`. No deferred fixes.
2. UI changes require screenshots in the PR body using `raw.githubusercontent.com` absolute URLs.
3. One concern per commit. Do not mix refactoring with features or bug fixes.
4. English only in code, comments, commit messages, docs, and skill files. Exception: `README_zh.md` and explicitly Chinese README files.
5. Screenshots, demos, and test evidence must use real trace data from `.traces/`, never mocks or synthetic data.
6. Pre-work checklist is required before coding; pre-PR checklist is required before opening or merging.
7. After changes, you must `git add`, `git commit`, and `git push origin <branch>`.
8. You must create the GitHub PR with `gh pr create`; work is not done until PR exists.
