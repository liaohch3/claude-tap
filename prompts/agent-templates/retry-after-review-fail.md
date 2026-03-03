# Retry After Review Fail

Task `{task_id}` was human-rejected.

Produce a corrected iteration:
1. List the review issues you are fixing.
2. Apply focused fixes only.
3. Re-run relevant validation.
4. Emit `[WD_PROGRESS] ...` after each substantial fix/validation step.
5. Summarize what changed and why it resolves the rejection.
6. Emit `[WD_DONE] retry_ready_for_review` when retry is complete.

Recent pane tail:

```text
{pane_tail}
```
