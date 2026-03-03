---
status: active
---

# TODO：支持 /v1/responses 的 Codex WebSocket 传输

**日期：** 2026-02-28
**优先级：** 中
**状态：** 计划中

## 背景

Codex CLI v0.106.0+ 默认对 `/v1/responses` API 调用使用 WebSocket 传输
（`responses_websockets` 和 `responses_websockets_v2` 特性）。当前 claude-tap
通过自动注入 `--disable responses_websockets` 的方式绕过该行为并强制使用 HTTP，
从而使现有 HTTP reverse proxy 能捕获全部请求。

这个方案可用，但不理想。WebSocket 传输可能更快，且未来 Codex 版本很可能会
成为默认/唯一路径。

## 目标

在 claude-tap 的 reverse proxy mode 中原生支持 WebSocket 拦截，使 Codex
可使用默认 WebSocket 传输，同时 claude-tap 仍能捕获全部 API 调用。

## 方案选项

### 方案 A：WebSocket MITM proxy
- 拦截到 `/v1/responses` 的 WebSocket upgrade 请求
- 代理 WebSocket 连接并记录全部 frame
- 将 frame 重组为相同 trace 格式（请求体 + SSE 等价事件）
- 优点：对 Codex 透明，无需注入 CLI flag
- 缺点：实现更复杂，需要处理 WS frame 重组

### 方案 B：带 CONNECT 隧道的 forward proxy
- 使用 forward proxy mode（HTTP_PROXY/HTTPS_PROXY）并做 TLS 拦截
- 在 TLS 层同时拦截 HTTP 与 WebSocket 流量
- 优点：适用于所有传输类型
- 缺点：需要注入 TLS 证书，系统组成更复杂

### 方案 C：混合方案（检测并自适应）
- 检测 Codex 是否使用 WebSocket（检查 Upgrade headers）
- 若是 WebSocket：代理 WS 连接并记录 frame
- 若是 HTTP：使用现有 streaming proxy 路径
- 优点：向后兼容，可覆盖任意 Codex 版本
- 缺点：需要维护两条代码路径

## 实现说明

- Responses API 的 WebSocket frame 很可能遵循类似 SSE 的事件结构
- 需要进一步确认 Codex 使用的具体 WebSocket 消息格式
- `aiohttp`（已是依赖）支持 WebSocket proxying
- `--disable` flag 绕过方案应保留为 fallback 选项

## 参考

- 修复 commit：`a0e00e2`（在 reverse mode 下禁用 websocket 传输）
- 错误经验：`docs/error-experience/entries/2026-02-28-codex-reverse-websocket-capture-gap.md`
- Codex CLI flags：`--enable/--disable responses_websockets[_v2]`
