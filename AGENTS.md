## Pre-commit CI checks

Before every `git commit`, run these checks locally (mirrors GitHub CI):

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest tests/ -x --timeout=60
```

All three must pass before committing. If format fails, run `uv run ruff format .` first.

## E2E Validation Requirements

If a change affects proxying, trace capture, CLI session flow, auth handling, or other end-to-end behavior, run real E2E validation before opening a PR.

Preferred commands:

```bash
# Full real E2E suite (requires Claude CLI auth)
uv run pytest tests/e2e/ --run-real-e2e --timeout=300

# Targeted real E2E case
uv run pytest tests/e2e/test_real_proxy.py::TestRealProxy::test_single_turn --run-real-e2e --timeout=180
```

Pragmatic manual alternatives:

```bash
scripts/run_real_e2e.sh
scripts/run_real_e2e_tmux.sh
```

If real E2E cannot run (for example due missing auth/token), explicitly document the reason and residual risk in the PR description.

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

Before opening or merging a PR, also run:

```bash
git rebase origin/main     # Rebase onto latest main
uv lock --check            # Ensure lockfile is consistent
uv run pytest tests/ -x --timeout=60  # Re-verify after rebase
```

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
| Prefer POSIX shell tools in scripts | Scripts must run in bare environments |
| Use `grep -F` for fixed-string matches in scripts | Portable replacement for `rg` in CI/fresh machines |
| Read package version from metadata | Avoid stale hardcoded version strings |

### DON'T

| Anti-pattern | Why |
|--------------|-----|
| Leave commented-out code | Use version control, not comments |
| Add speculative abstractions | YAGNI — wait until you need it |
| Suppress linter warnings without justification | Fix the issue or document why it's a false positive |
| Commit generated files | Regenerate from source |
| Mix refactoring with feature work | One concern per commit |
| Add backwards-compat shims for unused code | Just delete it |
| Depend on non-portable shell tools without checks | `rg`/`jq`/`fd` may be missing in CI or fresh machines |

## Runtime Safety Rules

- If code uses `tcsetpgrp`/terminal foreground handoff, handle `SIGTTOU` when reclaiming the parent foreground process group.
- Treat the highest Python version in CI as the compatibility ceiling (currently Python 3.13), and validate behavior there for runtime-sensitive changes.
- Certificate generation for TLS tests/runtime must include SKI/AKI extensions for Python 3.13 compatibility.
- For certificate/proxy/security-sensitive changes, validate tests on Python 3.13 locally when available (CI also enforces this).

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

## PR Requirements for UI Changes

If a PR changes UI layout, styles, interaction flow, or rendered content, include screenshots in the PR description.

- Provide at least one screenshot per changed screen/state.
- For visual diffs, include before/after screenshots when possible.
- Include mobile screenshots when mobile behavior is affected.

## Brain + Hands Protocol

- **Claude Code (Opus)** = planning brain. Makes architecture decisions, designs APIs,
  chooses patterns, reviews code.
- **Codex** = execution hands. Writes boilerplate, runs commands, applies mechanical changes.

Never delegate architecture decisions to execution tools. The brain decides *what* and *why*;
the hands do *how*.
