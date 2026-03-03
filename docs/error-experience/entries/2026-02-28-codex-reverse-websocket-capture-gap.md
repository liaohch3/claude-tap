# Codex Reverse Mode 可能遗漏 Responses 流量

**日期：** 2026-02-28  
**严重级别：** 高  
**标签：** codex, reverse-proxy, websocket, trace-capture

## 问题

在 `--tap-client codex` 的 reverse mode 中，部分运行只捕获到 `/v1/models`，
viewer 中 token usage 为 0。后续对话流量未能稳定捕获为 `/v1/responses`。

## 根因

Codex 在交互/会话流中可能尝试基于 websocket 的 Responses 路径。
当用户配置或特性开关启用 websocket 行为时，reverse-mode base URL 路由会出现
trace 捕获不一致。

## 修复

- 在 Codex 的 reverse mode 下自动注入：
  - `--disable responses_websockets`
  - `--disable responses_websockets_v2`
- 保留用户意图：若用户已通过 `--enable`、`--disable` 或 `-c/--config features.<name>=...`
  显式覆盖特性，不再强制覆盖。

## 验证

- 增加 E2E 断言：reverse-mode 启动包含 websocket-disable flags。
- 增加 E2E 断言：尊重用户显式 feature override。
- 运行完整 gate 检查（`ruff`、format check、`pytest tests/ -x --timeout=60`）。

## 经验

为了保证 proxy 捕获可靠性，不要假设 Codex 传输永远是 HTTP POST。
在 reverse proxy 场景下，应显式将传输特性固定到可捕获路径，并在测试中让覆盖行为具备确定性。
