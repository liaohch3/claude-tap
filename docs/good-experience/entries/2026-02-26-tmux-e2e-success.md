# tmux 真实 E2E 成功模式

**日期：** 2026-02-26
**标签：** e2e, tmux, testing, verification

## 问题

tmux `send-keys` 在真实交互式 E2E 运行中，无法稳定向 Claude Code TUI 提交 prompt。

## 根因

- 部分流程假设 `rg` 可用于断言，但在一些环境中并不存在。
- 对 Claude Code TUI 在 tmux 下的提交行为建模错误；正确提交键是 `Enter`。

## 解决方案

- 将脆弱的 `rg` 断言替换为可移植的 `grep -F`。
- 将默认提交行为统一为 `Enter`。
- 加入“未命中重试提交”逻辑，降低瞬时输入时序失败。

## 验证

通过 JSONL 断言验证：

1. 两个 prompt 都出现在 trace 数据中。
2. `/v1/messages` 调用至少为 2 次。
3. 至少一个响应内容块为 `tool_use`。
4. 生成了 HTML viewer 产物。

## 结果

- pytest real E2E 用例 `7/7` 通过。
- tmux 交互式 E2E 通过，且确认捕获到 `tool_use`。
- 生成了 asciinema 录制。
- 已截取生成的 HTML viewer browser 截图。

## 经验

在 tmux 下做真实 Claude TUI 自动化时，可移植性与输入语义比“巧工具”更重要：
优先 `grep -F`，使用 `Enter`，并通过 trace 产物验证。
