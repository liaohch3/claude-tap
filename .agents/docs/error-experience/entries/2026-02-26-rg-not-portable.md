# `rg`（ripgrep）并非所有环境都可用

**日期：** 2026-02-26
**严重级别：** 低
**标签：** portability, shell, tooling

## 问题

Shell 脚本 `run_real_e2e_tmux.sh` 使用 `rg`（ripgrep）做 JSONL 断言。
在未安装 ripgrep 或不在 `$PATH` 的环境里，脚本会静默失败或给出误导结果。

## 根因

`rg` 是 Rust 工具，不属于 POSIX，也不是 macOS 默认安装。
CI runner、Codex sandbox 和新初始化机器都可能缺少它。

## 修复

将全部 `rg` 调用替换为 `grep -F`（固定字符串匹配），后者是 POSIX 标准且普遍可用。

```bash
# Before (fragile)
rg '"tool_use"' "$JSONL_FILE"

# After (portable)
grep -F '"tool_use"' "$JSONL_FILE"
```

## 经验

**在 shell 脚本中优先使用 POSIX 标准工具**：`grep`、`sed`、`awk`、`find`、`cut`。
`rg`、`fd`、`jq` 等工具应留给交互式使用，或明确声明为依赖。
脚本必须能在裸环境运行。
