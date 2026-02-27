# Claude Code Bridge

This repository uses a single source of truth for engineering rules:

- Follow [`../AGENTS.md`](../AGENTS.md) for all workflow, testing, and review requirements.

Skill layout for multi-agent compatibility:

- Canonical skills directory: `.agents/skills/`
- Claude compatibility path: `.claude/skills -> ../.agents/skills` (symlink)

Do not duplicate policy text in this file. Keep all normative rules in `AGENTS.md`.
