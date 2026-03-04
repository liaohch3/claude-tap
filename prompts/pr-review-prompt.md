# PR Review Task

You are reviewing Pull Request #{pr_number} for the `claude-tap` project.

## Project Standards

Read and enforce these project standards (all files are in the repo):
- `AGENTS.md` - hard rules index
- `docs/standards/hard-rules.md`
- `docs/standards/validation-and-gates.md`
- `docs/standards/e2e-and-evidence.md`
- `docs/standards/screenshot-standards.md`
- `docs/standards/coding-and-runtime.md`
- `docs/standards/workflow-and-review.md`

## PR Information

- **Title**: {pr_title}
- **Branch**: `{head_ref}` -> `{base_ref}`
- **Description**:

{pr_body}

## Diff

```diff
{diff_text}
```

## Review Requirements

1. Review the diff carefully and identify bugs, regressions, security issues, and maintainability risks.
2. Evaluate test coverage and call out missing scenarios.
3. Check PR description requirements, including screenshot evidence for UI changes.
4. Check title and commit conventions expected by the repository standards.
5. Enforce all rules from `AGENTS.md` and `docs/standards/`.
6. Provide a recommendation: `APPROVE`, `REQUEST_CHANGES`, or `COMMENT`.

## Output Requirements

Output review text only (Markdown), using {output_language}.
Do not run `gh` commands.

The output must include one explicit line in this exact format:

```text
Decision: APPROVE
```

Allowed values are:
- `Decision: APPROVE`
- `Decision: REQUEST_CHANGES`
- `Decision: COMMENT`

Use this review structure:

```markdown
## Automated Code Review

### Summary
(One-sentence summary)

### Findings

#### Critical
(Write "None" if empty)

#### High
...

#### Medium
...

#### Low
...

### Decision
(Repeat APPROVE / REQUEST_CHANGES / COMMENT and concise reason)

---
*Powered by local PR Review Bot*
```
