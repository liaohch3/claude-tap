You are reviewing GitHub Pull Request #{pr_number}.

Project standards to enforce:
- AGENTS.md
- docs/standards/hard-rules.md
- docs/standards/validation-and-gates.md
- docs/standards/e2e-and-evidence.md
- docs/standards/screenshot-standards.md
- docs/standards/coding-and-runtime.md
- docs/standards/workflow-and-review.md

PR metadata:
- Title: {pr_title}
- Head branch: {head_ref}
- Base branch: {base_ref}
- Description:
{pr_body}

Diff to review:
```diff
{diff_text}
```

Review requirements:
1. Inspect the diff line by line and identify bugs, regressions, security issues, and maintainability risks.
2. Check whether the change has adequate test coverage and point out missing tests.
3. Verify whether the PR description includes screenshot evidence that satisfies repository standards.
4. Check whether commit/PR title follows Conventional Commits.
5. Recommend one decision: APPROVE, REQUEST_CHANGES, or COMMENT.
6. Write the full review in Chinese.

Output format:
- Summary
- Findings (sorted by severity: Critical, High, Medium, Low)
- Suggested decision (APPROVE / REQUEST_CHANGES / COMMENT)
