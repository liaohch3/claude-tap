# Resume After Stuck

Task `{task_id}` was restarted after watchdog detected `{reason}`.

Resume execution with this plan:
1. Recover context from repository and task state.
2. State the next actionable step.
3. Continue execution without waiting.
4. Emit `[WD_PROGRESS] ...` after each significant step.
5. Emit `[WD_BLOCKER] ...` if blocked and include the concrete next unblocking action.
6. Emit `[WD_DONE] resumed_ready_for_review` when complete.

Recent pane tail before restart:

```text
{pane_tail}
```
