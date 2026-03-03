# PR Review Task

You are reviewing Pull Request #{pr_number} for the `claude-tap` project.

## Project Standards

Read and enforce these project standards (all files are in the repo):
- `AGENTS.md` — hard rules index
- `docs/standards/hard-rules.md`
- `docs/standards/validation-and-gates.md`
- `docs/standards/e2e-and-evidence.md`
- `docs/standards/screenshot-standards.md`
- `docs/standards/coding-and-runtime.md`
- `docs/standards/workflow-and-review.md`

## PR Information

- **Title**: {pr_title}
- **Branch**: `{head_ref}` → `{base_ref}`
- **Description**:

{pr_body}

## Diff

```diff
{diff_text}
```

## Review Requirements

1. **逐行审查 diff** — 找 bug、回归、安全问题、可维护性风险
2. **检查测试覆盖** — 是否有足够的测试？缺少哪些场景？
3. **检查 PR 描述** — 是否有截图证据（如果涉及 UI 变更）
4. **检查标题规范** — 是否符合 Conventional Commits
5. **对照项目标准** — 读 AGENTS.md 和 docs/standards/ 确认合规性
6. **给出决策建议** — APPROVE / REQUEST_CHANGES / COMMENT

## Output Format (严格遵守)

用中文输出，格式如下：

### Summary
（一句话总结 PR 做了什么，质量如何）

### Findings

#### Critical
（列出，没有则写"无"）

#### High
（列出）

#### Medium
（列出）

#### Low
（列出）

### Suggested Decision
（APPROVE / REQUEST_CHANGES / COMMENT，附一句理由）
