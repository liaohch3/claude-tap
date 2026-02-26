## Pre-commit CI checks

Before every `git commit`, run these checks locally (mirrors GitHub CI):

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest tests/ -x --timeout=60
```

All three must pass before committing. If format fails, run `uv run ruff format .` first.

## Language

All code, comments, commit messages, docs, and skill files in this project must be in English. The only exceptions are `README_zh.md` and other explicitly Chinese README files.

## Pre-work Checklist

Before any code change, run:

```bash
git diff --stat            # Check for uncommitted changes
git log --oneline -10      # Understand recent history
git fetch origin           # Get latest remote state
```

Ensure you are working on a clean, up-to-date branch.

## Compounding Engineering

Record lessons learned so they compound over time:

- **Error experience** (mistakes, failures): `docs/error-experience/entries/YYYY-MM-DD-<slug>.md`
- **Good experience** (wins, patterns): `docs/good-experience/entries/YYYY-MM-DD-<slug>.md`
- **Summaries**: `docs/error-experience/summary/entries/` and `docs/good-experience/summary/entries/`
- **Plans**: `docs/plans/`
- **Guides**: `docs/guides/`

After encountering a significant bug, CI failure, or discovering a useful pattern,
create an entry documenting what happened, root cause, and the lesson.

## Coding Standards

### DO

| Practice | Why |
|----------|-----|
| Delete dead code | Dead code misleads readers and rots |
| Fix root cause of test failures | Patching symptoms creates fragile tests |
| Use existing patterns | Consistency beats novelty |
| Modify only relevant files | Minimize blast radius |
| Trust type invariants | Don't add redundant runtime checks for typed values |
| Keep functions focused | One function, one purpose |

### DON'T

| Anti-pattern | Why |
|--------------|-----|
| Leave commented-out code | Use version control, not comments |
| Add speculative abstractions | YAGNI — wait until you need it |
| Suppress linter warnings without justification | Fix the issue or document why it's a false positive |
| Commit generated files | Regenerate from source |
| Mix refactoring with feature work | One concern per commit |
| Add backwards-compat shims for unused code | Just delete it |

## Worktree Workflow

Use git worktrees for isolated feature development:

```bash
# Create worktree
git worktree add -b feat/<name> /tmp/claude-tap-<name> main

# Develop and test in worktree
cd /tmp/claude-tap-<name>
uv run pytest tests/ -x --timeout=60

# Merge back (fast-forward only)
cd /path/to/claude-tap
git merge --ff-only feat/<name>

# Clean up
git worktree remove /tmp/claude-tap-<name>
git branch -d feat/<name>
```

## Code Review

Before every commit:

1. `uv run ruff check .` — lint passes
2. `uv run ruff format --check .` — format passes
3. `uv run pytest tests/ -x --timeout=60` — tests pass
4. `git diff` — review every changed line before staging
5. Verify scope: only files relevant to the task were modified

## Brain + Hands Protocol

- **Claude Code (Opus)** = planning brain. Makes architecture decisions, designs APIs,
  chooses patterns, reviews code.
- **Codex** = execution hands. Writes boilerplate, runs commands, applies mechanical changes.

Never delegate architecture decisions to execution tools. The brain decides *what* and *why*;
the hands do *how*.

## Codex Sandbox Limitations

The Codex `--full-auto` sandbox has known restrictions:

| Blocked | Workaround |
|---------|------------|
| `git commit` / `git fetch` (`.git/` writes) | Commit outside sandbox after Codex finishes |
| `tmux` / `screen` (`/private/tmp` socket) | Run terminal multiplexer tasks via external exec |
| `rg` / non-POSIX tools may be absent | Use `grep -F`, `sed`, `awk` in scripts |

**Rule:** Never include `git commit` in a Codex task prompt. Always plan a post-Codex
commit step.

## Shell Script Portability

Prefer POSIX-standard utilities in all shell scripts:

- ✅ `grep -F`, `sed`, `awk`, `find`, `cut`, `sort`, `wc`
- ❌ `rg`, `fd`, `bat`, `delta` (may not exist on CI/sandbox)

Reserve non-POSIX tools for interactive use or explicitly declared dependencies.

## Repo Cleanup Rules

When removing stale files:

1. **Migrate before delete**: Extract valuable content (design rationale, decisions)
   into `docs/guides/` before removing source files.
2. **Never silently drop**: If a file had useful context, create a guide or add to
   existing docs.
3. **Keep CHANGELOG aligned**: When versions advance, ensure CHANGELOG covers every
   release (reconstruct from `git log` if needed).
