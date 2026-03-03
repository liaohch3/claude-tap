# Corrective Loop

Task `{task_id}` is in `{state}` due to `{reason}`.

You must self-correct now:
1. Briefly summarize the blocker.
2. Pick the smallest valid next step.
3. Execute that step immediately.
4. Emit one line with `[WD_BLOCKER] ...` describing the current blocker.
5. Emit one line with `[WD_PROGRESS] ...` after each significant step.
6. When fixed and ready for review, emit `[WD_DONE] ready_for_review`.

Recent pane tail:

```text
{pane_tail}
```
