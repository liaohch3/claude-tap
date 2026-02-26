# SIGTTOU Suspend on Exit After Child Process

**Date:** 2026-02-26
**Severity:** High
**Tags:** signal, tty, process-group, tcsetpgrp, exit-path

## Problem

After Claude Code exits, `claude-tap` tries to print a summary and generate
HTML output but gets suspended with `suspended (tty output)` every time.

## Impact

High. This was the #1 user-facing bug: users could never get HTML output
because the process was suspended before the finalization path completed.

## Root Cause

`claude-tap` gives terminal foreground control to the Claude Code child process
via `tcsetpgrp`. When the child exits, `claude-tap` is still in a background
process group. Any terminal write triggers `SIGTTOU`, which suspends the
process, so the `finally` block (HTML generation and summary) never runs.

## Fix

Ignore `SIGTTOU` before calling `tcsetpgrp` to reclaim the foreground group,
then restore the original signal handler afterward.

## Lesson Learned

When using process groups with `tcsetpgrp`, always handle `SIGTTOU` around the
transition back to the parent foreground group.
