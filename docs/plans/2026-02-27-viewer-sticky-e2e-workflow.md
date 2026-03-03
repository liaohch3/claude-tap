---
status: completed
---

# Viewer Sticky Action Bar + 验证工作流计划

## 问题

`claude_tap/viewer.html` 中 detail pane 的操作按钮行在用户向下滚动时会消失。
这会降低重复操作（`Request JSON`、`cURL`、`Diff with Prev`）的效率。

## 范围

- 在滚动 detail 内容时保持 action bar 可见。
- 更新仓库工作流指引，确保未来 PR 包含：
  - 真实 E2E 验证期望
  - 对影响 UI 变更的截图要求
- 产出 review 证据（测试输出 + 截图）。

## 执行顺序

1. 定义目标行为与范围。
2. 实施最小化 UI 变更。
3. 运行受影响行为的定向测试。
4. 运行 E2E/browser 验证并收集截图。
5. 运行完整项目质量 gate。
6. 准备包含证据的 PR。
7. 记录经验教训。

## 变更摘要

- `claude_tap/viewer.html`
  - 让 `.action-bar` 在 detail 滚动容器中保持 sticky。
- `AGENTS.md`
  - 新增 `E2E Validation Requirements` 章节。
  - 新增 `PR Requirements for UI Changes` 章节。

## 验证

- Unit/integration 测试：`uv run pytest tests/ -x --timeout=60`
- Lint/format 检查：
  - `uv run ruff check .`
  - `uv run ruff format --check .`
- UI 证据：
  - 顶部状态截图
  - 滚动状态截图，确认 sticky action bar 仍可见

## 范围外

- Viewer 布局的功能重设计。
- 非相关依赖更新。
