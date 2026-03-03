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

## Output: 直接提交 Review

审查完成后，**直接用 gh CLI 提交 review**，不要写到文件：

```bash
# 如果有具体行的问题，用 inline comment：
gh pr review {pr_number} --request-changes --body "你的 review 内容（Markdown 格式，中文）"

# 或者如果一切 OK：
gh pr review {pr_number} --approve --body "LGTM. 简要说明..."

# 或者只是建议：
gh pr comment {pr_number} --body "你的评论"
```

### Review 格式要求（写在 --body 里）

用中文，Markdown 格式：

```
## 🤖 自动 Code Review

### Summary
（一句话总结）

### Findings

#### Critical
（没有则写"无"）

#### High
...

#### Medium
...

#### Low
...

### Decision
（APPROVE / REQUEST_CHANGES / COMMENT + 理由）

---
*⚡ Powered by local PR Review Bot*
```

**重要**：你必须自己执行 `gh pr review` 或 `gh pr comment` 命令。这是你的最终输出。
