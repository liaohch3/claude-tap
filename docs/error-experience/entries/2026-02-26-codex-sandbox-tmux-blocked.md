# Codex Sandbox 阻止 tmux Socket 创建

**日期：** 2026-02-26
**严重级别：** 中
**标签：** codex, sandbox, tmux, environment

## 问题

在 Codex `--full-auto` 中运行基于 tmux 的 E2E 流程失败，因为 tmux 无法在
`/private/tmp` 下创建或访问其 socket 路径（permission denied）。

## 影响

- Codex 可以更新代码和文档，但不能直接执行 tmux 交互测试。
- 依赖 tmux 的端到端验证必须在 Codex sandbox 外完成。

## 变通方案

- 用 Codex 处理代码编辑、重构以及可静态/可测试逻辑。
- 在 Codex 外执行依赖 tmux 的验证（例如 OpenClaw exec 或本地 shell）。
- 外部验证后，再把结果回填到仓库文档/测试中。

## 经验

需要系统级终端复用器（`tmux`、`screen`）的任务，无法完整委托给受限的 Codex sandbox。
必须明确划分 sandbox 安全工作与外部执行职责。
