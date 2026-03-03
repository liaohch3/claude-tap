# PR #1 过期基线分支导致 CI 失败

**日期：** 2026-02-25
**严重级别：** 中
**标签：** git, CI, uv.lock, rebase

## 发生了什么

PR #1（新增 `--tap-host` 功能）在 CI 中测试失败，原因是它基于过期的 `main` 分支。
该分支从 `bc7d344` 分叉，而 `main` 已推进到 `bd7ec3f`。两个版本的 `uv.lock`
不兼容，导致依赖解析失败，测试报错。

## 根因

在打开 PR 前，feature 分支没有 rebase 到最新 `main`。
`uv.lock` 已产生分叉，过期版本无法解析正确依赖集。

## 影响

- 原本正确的 PR 在 CI 中失败
- 需要额外一次 rebase 循环修复
- 合并延迟了一个 review 回合

## 经验

**在打开或合并 PR 前，始终先 rebase 到最新 `main`。**

防止复发的 checklist：
1. 开 PR 前：`git fetch origin && git rebase origin/main`
2. 验证 `uv.lock` 最新：`uv lock --check`
3. rebase 后本地运行完整测试：`uv run pytest tests/ -x --timeout=60`

## 相关

- PR：#1
- Commits：bc7d344..bd7ec3f
