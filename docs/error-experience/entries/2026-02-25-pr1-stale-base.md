# PR #1 Stale Base Branch Caused CI Failure

**Date:** 2026-02-25
**Severity:** Medium
**Tags:** git, CI, uv.lock, rebase

## What Happened

PR #1 (adding the `--tap-host` feature) had tests fail in CI because it was based
on a stale `main` branch. The branch was forked from `bc7d344` while `main` had
advanced to `bd7ec3f`. The `uv.lock` file was incompatible between the two versions,
causing dependency resolution to fail and tests to error out.

## Root Cause

The feature branch was not rebased onto the latest `main` before opening the PR.
The `uv.lock` file had diverged, and the stale version could not resolve the correct
dependency set.

## Impact

- CI tests failed on an otherwise correct PR
- Required an extra rebase cycle to fix
- Delayed merge by one review round

## Lesson Learned

**Always rebase onto latest `main` before opening or merging a PR.**

Checklist to prevent recurrence:
1. Before opening a PR: `git fetch origin && git rebase origin/main`
2. Verify `uv.lock` is up to date: `uv lock --check`
3. Run the full test suite locally after rebase: `uv run pytest tests/ -x --timeout=60`

## Related

- PR: #1
- Commits: bc7d344..bd7ec3f
