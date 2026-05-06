# 子进程退出后因 SIGTTOU 被挂起

**日期：** 2026-02-26
**严重级别：** 高
**标签：** signal, tty, process-group, tcsetpgrp, exit-path

## 问题

Claude Code 退出后，`claude-tap` 尝试打印摘要并生成 HTML 输出，
却每次都触发 `suspended (tty output)`。

## 影响

高。这是用户感知最强的问题：进程在收尾路径完成前就被挂起，
用户始终拿不到 HTML 输出。

## 根因

`claude-tap` 通过 `tcsetpgrp` 把终端前台控制权交给 Claude Code 子进程。
当子进程退出时，`claude-tap` 仍处于后台进程组。任何终端写操作都会触发 `SIGTTOU`，
导致进程被挂起，因此 `finally` 块（HTML 生成与摘要）无法运行。

## 修复

在调用 `tcsetpgrp` 夺回前台进程组前先忽略 `SIGTTOU`，
之后恢复原始 signal handler。

## 经验

只要使用 `tcsetpgrp` 进行进程组切换，在切回父进程前台组时就必须处理 `SIGTTOU`。
