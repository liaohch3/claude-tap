---
owner: claude-tap-maintainers
last_reviewed: 2026-03-03
source_of_truth: AGENTS.md
---

# Worktree Workflow

Use git worktrees for isolated feature development:

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

Before each commit:

1. `uv run ruff check .`
2. `uv run ruff format --check .`
3. `uv run pytest tests/ -x --timeout=60`
4. `git diff` and review each changed line.
5. Confirm only relevant files changed.

# Compounding Engineering

Record lessons learned:

- Error experience: `docs/error-experience/entries/YYYY-MM-DD-<slug>.md`
- Good experience: `docs/good-experience/entries/YYYY-MM-DD-<slug>.md`
- Summaries: `docs/error-experience/summary/entries/` and `docs/good-experience/summary/entries/`
- Plans: `docs/plans/`
- Guides: `docs/guides/`

Create an entry after significant bug, CI failure, or useful pattern discovery with root cause and lesson.

# Brain + Hands Protocol

- Claude Code (Opus): planning brain, architecture/API/pattern/review decisions.
- Codex: execution hands, boilerplate/commands/mechanical edits.

Do not delegate architecture decisions to execution tools.
