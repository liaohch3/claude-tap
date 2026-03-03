---
owner: claude-tap-maintainers
last_reviewed: 2026-03-03
source_of_truth: AGENTS.md
---

# 硬性规则

以下规则是强制性的。若你无法遵守，请停止并说明原因。

1. 每次 commit 前执行 gate 检查：`ruff check`、`ruff format --check`、`pytest`。不得延期修复。
2. UI 变更要求在 PR 正文中提供使用 `raw.githubusercontent.com` 绝对 URL 的截图。
3. 每个 commit 只处理一个关注点。不要把 refactor 与 feature 或 bug fix 混在一起。
4. 代码、注释、commit message、文档和 skill 文件仅使用英文。例外：`README_zh.md` 与明确为中文的 README 文件。
5. 截图、演示和测试证据必须使用 `.traces/` 中的真实 trace 数据，禁止 mock 或合成数据。
6. 编码前必须完成 pre-work checklist；开 PR 或合并前必须完成 pre-PR checklist。
7. 变更后必须执行 `git add`、`git commit` 和 `git push origin <branch>`。
8. 必须使用 `gh pr create` 创建 GitHub PR；PR 未创建前，工作不算完成。
