# Codex Sandbox 无法执行 Git Commit

**日期：** 2026-02-26
**严重级别：** 中
**标签：** codex, sandbox, git, environment

## 问题

Codex `--full-auto` sandbox 会阻止对 `.git/index.lock` 和
`.git/FETCH_HEAD` 的写访问，导致 `git commit` 与 `git fetch` 无法完成。

## 影响

- Codex 可以通过 `git add` 暂存文件，但无法 commit 或 fetch。
- 任何要求最后提交的工作流都必须转到外部环境执行。

## 变通方案

- 用 Codex 完成代码编辑、重构、测试和 lint 检查。
- Codex 完成后，在 sandbox 外执行 `git add -A && git commit`
  （通过 OpenClaw exec 或本地 shell）。
- 不要在 Codex 任务提示中要求 `git commit`；它会静默失败或直接报错。

## 经验

Codex sandbox 会限制 `.git/` 目录写入。把会产生文件变更的任务交给 Codex 时，
始终规划一个 Codex 后置 commit 步骤。将流程拆分为：
Codex 编辑 → 外部 git commit → push。
