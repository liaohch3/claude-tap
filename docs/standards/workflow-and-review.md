---
owner: claude-tap-maintainers
last_reviewed: 2026-03-03
source_of_truth: AGENTS.md
---

# Worktree 工作流

使用 git worktree 进行隔离的 feature 开发：

```bash
git worktree add -b feat/<name> /tmp/claude-tap-<name> main
cd /tmp/claude-tap-<name>
uv run pytest tests/ -x --timeout=60
cd /path/to/claude-tap
git merge --ff-only feat/<name>
git worktree remove /tmp/claude-tap-<name>
git branch -d feat/<name>
```

# Code Review Checklist

每次 commit 前：

1. `uv run ruff check .`
2. `uv run ruff format --check .`
3. `uv run pytest tests/ -x --timeout=60`
4. `git diff` 并检查每一行变更。
5. 确认只改动了相关文件。

# 复利式工程实践

记录经验教训：

- 错误经验：`docs/error-experience/entries/YYYY-MM-DD-<slug>.md`
- 正向经验：`docs/good-experience/entries/YYYY-MM-DD-<slug>.md`
- 汇总：`docs/error-experience/summary/entries/` 与 `docs/good-experience/summary/entries/`
- 计划：`docs/plans/`
- 指南：`docs/guides/`

在出现重大 bug、CI 失败或发现有价值模式后，创建一条条目并记录根因与经验。

# Brain + Hands 协议

- Claude Code (Opus)：规划大脑，负责架构/API/模式/review 决策。
- Codex：执行双手，负责样板代码/命令/机械性编辑。

不要把架构决策委托给执行工具。
