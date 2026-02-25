# Engineering Practices Guide

This document codifies the engineering standards for the claude-tap project.

## Python Code Style

- **Linter/formatter:** [ruff](https://docs.astral.sh/ruff/)
- **Line length:** 120 characters
- **Target version:** Python 3.11+
- **Lint rules:** `E` (errors), `F` (pyflakes), `W` (warnings), `I` (import sorting)
- **Ignored rules:** `E501` (line length — enforced by formatter instead)

Run locally:
```bash
uv run ruff check .          # Lint
uv run ruff format --check . # Check formatting
uv run ruff format .         # Auto-fix formatting
```

## Testing Strategy

### Test Layers

| Layer | Location | What It Tests | Runs In CI | External Deps |
|-------|----------|---------------|------------|---------------|
| **Unit** | `tests/test_diff_matching.py` | Pure logic (diff matching, parsing) | Yes | None |
| **Mock E2E** | `tests/test_e2e.py` | Full pipeline with fake upstream + fake Claude | Yes | None |
| **Browser integration** | `tests/test_nav_browser.py` | HTML viewer JavaScript logic | Yes (with Playwright) | Playwright |
| **Real E2E** | `tests/e2e/` | Actual Claude CLI integration | No (opt-in) | Claude CLI, API key |

### Running Tests

```bash
# Full CI suite (unit + mock E2E)
uv run pytest tests/ -x --timeout=60

# Specific test file
uv run pytest tests/test_e2e.py -x --timeout=120

# Real E2E tests (requires claude CLI)
uv run pytest tests/e2e/ --run-real-e2e --timeout=300

# All tests including real E2E
uv run pytest tests/ --run-real-e2e --timeout=300
```

### Writing New Tests

- Use `pytest` fixtures for setup/teardown (see `conftest.py`)
- Use `tempfile.mkdtemp()` for temporary directories — always clean up
- For async tests, use `pytest-asyncio` (configured with `asyncio_mode = "auto"`)
- Mark slow tests with `@pytest.mark.slow`
- Mark integration tests with `@pytest.mark.integration`

## Commit Conventions

- Write commit messages in English
- Use imperative mood: "add feature" not "added feature"
- Prefix with type: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`
- Keep subject line under 72 characters
- Add body for non-trivial changes explaining "why"

Examples:
```
feat: add --tap-host flag for custom bind address
fix: handle malformed SSE events without crashing
test: add real E2E tests with Claude CLI integration
docs: update engineering practices guide
```

## Pre-Work Checklist

Before making any code change:

1. **Check repo state:**
   ```bash
   git diff --stat
   git log --oneline -10
   ```
2. **Ensure clean working tree** or stash changes
3. **Pull latest main:** `git fetch origin && git rebase origin/main`
4. **Verify tests pass:** `uv run pytest tests/ -x --timeout=60`

## Worktree Workflow for Features

Use git worktrees for isolated feature development:

```bash
# Create worktree for new feature
git worktree add -b feat/my-feature /tmp/claude-tap-my-feature main

# Work in the worktree
cd /tmp/claude-tap-my-feature
# ... make changes, run tests ...

# Merge back (fast-forward only)
cd /path/to/claude-tap
git merge --ff-only feat/my-feature

# Clean up
git worktree remove /tmp/claude-tap-my-feature
git branch -d feat/my-feature
```

Benefits:
- Main worktree stays clean
- Can switch between features without stashing
- Natural isolation prevents cross-contamination

## Code Review Process

Before committing:

1. **Lint:** `uv run ruff check .`
2. **Format:** `uv run ruff format --check .`
3. **Test:** `uv run pytest tests/ -x --timeout=60`
4. **Review diff:** `git diff` — read every changed line
5. **Verify scope:** Only changed files relevant to the task?

Before merging a PR:

1. All CI checks pass
2. No unresolved review comments
3. Branch is rebased on latest `main`
4. `uv.lock` is consistent

## Language

All code, comments, commit messages, docs, and skill files must be in English.
The only exceptions are `README_zh.md` and other explicitly Chinese README files.
