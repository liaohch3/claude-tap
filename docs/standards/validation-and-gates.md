---
owner: claude-tap-maintainers
last_reviewed: 2026-03-03
source_of_truth: AGENTS.md
---

# Pre-commit CI Checks

Before every commit, run:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest tests/ -x --timeout=60
```

All checks must pass. If formatting fails, run `uv run ruff format .` and re-check.

# Pre-work Checklist

Before any code change:

```bash
git diff --stat
git log --oneline -10
git fetch origin
```

Before opening or merging a PR:

```bash
git rebase origin/main
uv lock --check
uv run pytest tests/ -x --timeout=60
```
