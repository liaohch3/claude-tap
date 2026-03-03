# Resume After Stuck

Task `{task_id}` was restarted after watchdog detected `{reason}`.

Resume execution with this plan:
1. Recover context from repository and task state.
2. State the next actionable step.
3. Continue execution without waiting.
4. Emit a progress marker after each significant step.

Recent pane tail before restart:

```text
{pane_tail}
```
