---
owner: claude-tap-maintainers
last_reviewed: 2026-03-03
source_of_truth: AGENTS.md
---

# Pre-commit CI 检查

每次 commit 前运行：

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest tests/ -x --timeout=60
```

所有检查都必须通过。若格式检查失败，运行 `uv run ruff format .` 后重新检查。

# Pre-work Checklist

在进行任何代码变更之前：

```bash
git diff --stat
git log --oneline -10
git fetch origin
```

在打开或合并 PR 之前：

```bash
git rebase origin/main
uv lock --check
uv run pytest tests/ -x --timeout=60
```
