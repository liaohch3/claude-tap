# `rg` (ripgrep) Not Available in All Environments

**Date:** 2026-02-26
**Severity:** Low
**Tags:** portability, shell, tooling

## Problem

Shell script `run_real_e2e_tmux.sh` used `rg` (ripgrep) for JSONL assertions.
On environments where ripgrep is not installed or not in `$PATH`, the script
silently failed or gave misleading results.

## Root Cause

`rg` is a Rust-based tool that is not part of POSIX or macOS default installs.
CI runners, Codex sandboxes, and freshly provisioned machines may lack it.

## Fix

Replaced all `rg` calls with `grep -F` (fixed-string match), which is POSIX-standard
and universally available.

```bash
# Before (fragile)
rg '"tool_use"' "$JSONL_FILE"

# After (portable)
grep -F '"tool_use"' "$JSONL_FILE"
```

## Lesson Learned

**Prefer POSIX-standard utilities in shell scripts**: `grep`, `sed`, `awk`, `find`, `cut`.
Reserve `rg`, `fd`, `jq`, etc. for interactive use or when explicitly declared as
dependencies. Scripts must work on bare environments.
