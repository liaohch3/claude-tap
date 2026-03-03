# AGENTS 索引

本文件是贡献者规则的入口。详细策略文本位于 `docs/standards/*.md`。

## 不可协商规则

以下规则是强制性的，并会在 review 中执行：

1. 每次 commit 前运行 gate 检查：`uv run ruff check .`、`uv run ruff format --check .`、`uv run pytest tests/ -x --timeout=60`。
2. UI 变更要求在 PR 中提供使用 `raw.githubusercontent.com` 绝对 URL 的截图。
3. 每个 commit 只处理一个关注点（不要在同一 commit 中混合 refactor 与 feature/fix）。
4. 代码/注释/文档/commit 仅使用英文，`README_zh.md` 除外。
5. 证据必须使用 `.traces/` 中的真实 trace 数据（禁止合成 mock 截图/演示）。
6. 编码前必须执行 pre-work checklist，开 PR 前必须执行 pre-PR checklist。
7. 不要留下仅本地存在的工作；你必须执行 `git add`、`git commit` 和 `git push`。
8. 你必须使用 `gh pr create` 打开 GitHub PR。

## 标准目录

- 硬性规则与仓库策略：`docs/standards/hard-rules.md`
- 验证 gate 与必需命令：`docs/standards/validation-and-gates.md`
- E2E 与截图证据要求：`docs/standards/e2e-and-evidence.md`
- 截图采集与验证标准：`docs/standards/screenshot-standards.md`
- 编码与运行时安全规则：`docs/standards/coding-and-runtime.md`
- 工作流、review 与 Brain/Hands 协议：`docs/standards/workflow-and-review.md`
- 调试方法论与反模式：`docs/standards/debugging-standards.md`
- 标准文档元数据与维护流程：`docs/standards/README.md`

## 可读性检查

确定性的可读性检查由 `scripts/check_legibility.py` 实现，并在 CI 中通过 `.github/workflows/legibility.yml` 运行。
