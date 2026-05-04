---
name: pr-preflight
description: Full pre-PR merge-readiness check. Run this before opening or merging a pull request — it validates local gates (lint, format, tests), CI status, screenshot evidence, and PR metadata in one pass. Also useful for reviewing an existing PR's readiness.
user_invocable: true
---

# PR Preflight

One-command merge-readiness check that combines local gates, CI status, and PR policy checks. Mirrors what reviewers look for so issues are caught before review, not during.

## Check an existing PR

```bash
scripts/check_pr.sh <pr_number>
```

This runs:
1. **PR metadata** — fetches title, state, draft status, merge state, branch info
2. **CI checks** — counts pass/fail/pending GitHub Actions checks
3. **Local gates** — runs lint, format, and tests locally:
   - `uv run ruff check .`
   - `uv run ruff format --check .`
   - `uv run pytest tests/ -x --timeout=60`
4. **Screenshot evidence** — scans PR body for image links (required by project policy)
5. **Verdict** — `READY` or `NOT_READY` with specific reasons

### Options

| Flag | Purpose |
|------|---------|
| `--repo OWNER/REPO` | Override repository (default: auto-detect via `gh`) |
| `--no-tests` | Skip local test gates (useful when you just want CI + metadata check) |

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | All checks passed — ready to merge |
| 1 | Script error (missing tool, network failure) |
| 2 | Not ready — at least one check failed |

## Run local gates only (no PR needed)

If you haven't opened a PR yet and just want to validate locally:

```bash
uv run ruff check . && uv run ruff format --check . && uv run pytest tests/ -x --timeout=60
```

Or use the pre-commit hook (auto-runs lint on commit):

```bash
git config core.hooksPath .githooks
```

## What blocks merge

The script reports `NOT_READY` if any of these are true:
- PR is not in OPEN state
- PR is still a draft
- Merge state is not CLEAN or HAS_HOOKS
- Any CI check is failing
- Any CI check is still pending
- Local gates (lint/format/tests) fail
- No screenshot evidence in PR body

## Typical workflow

```bash
# 1. Make sure local gates pass
uv run ruff check . && uv run ruff format --check . && uv run pytest tests/ -x --timeout=60

# 2. Push and open PR
git push origin my-branch
gh pr create --title "feat: ..." --body "..."

# 3. Wait for CI, then run full preflight
scripts/check_pr.sh <pr_number>
```
