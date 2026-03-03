---
owner: claude-tap-maintainers
last_reviewed: 2026-03-03
source_of_truth: AGENTS.md
---

# Coding Standards

## Do

- Delete dead code.
- Fix root cause of test failures.
- Use existing patterns and keep scope limited to relevant files.
- Trust type invariants and avoid redundant runtime checks for typed values.
- Keep functions focused on one purpose.
- Prefer POSIX shell tools in scripts.
- Use `grep -F` for fixed-string matches in scripts.
- Read package version from metadata, not hardcoded strings.

## Do Not

- Leave commented-out code.
- Add speculative abstractions.
- Suppress linter warnings without justification.
- Commit generated files.
- Mix refactoring with feature work.
- Add compatibility shims for unused code.
- Depend on non-portable tools without checks (`rg`, `jq`, `fd` may be missing).

# Runtime Safety Rules

- If using `tcsetpgrp` foreground handoff, handle `SIGTTOU` when reclaiming parent foreground process group.
- Treat highest CI Python version (currently 3.13) as the compatibility ceiling for runtime-sensitive behavior.
- Certificate generation for TLS tests/runtime must include SKI/AKI extensions for Python 3.13 compatibility.
- For certificate/proxy/security-sensitive changes, validate on Python 3.13 locally when available.
