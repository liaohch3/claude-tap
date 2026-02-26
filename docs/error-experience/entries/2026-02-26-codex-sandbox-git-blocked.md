# Codex Sandbox Cannot Run Git Commit

**Date:** 2026-02-26
**Severity:** Medium
**Tags:** codex, sandbox, git, environment

## Problem

Codex `--full-auto` sandbox blocks write access to `.git/index.lock` and
`.git/FETCH_HEAD`, preventing `git commit` and `git fetch` from completing.

## Impact

- Codex can stage files with `git add` but cannot commit or fetch.
- Any workflow that requires committing at the end must defer to external execution.

## Workaround

- Use Codex for code edits, refactors, test runs, and lint checks.
- After Codex finishes, run `git add -A && git commit` outside the sandbox
  (via OpenClaw exec or local shell).
- Do not include `git commit` in Codex task prompts; it will fail silently or error out.

## Lesson Learned

The Codex sandbox restricts `.git/` directory writes. Always plan for a post-Codex
commit step when delegating tasks that produce file changes. Split the workflow:
Codex edits → external git commit → push.
