# Codex WS 超时且已验证 HTTPS 回退（PR #22）

**日期：** 2026-03-03
**标签：** codex, websocket, reverse-proxy, validation, fallback

## 背景

在用真实 Codex 运行验证 PR #22（`feat/ws-proxy`）时，我们需要对 WebSocket 传输行为和当前环境中的回退行为提供硬证据。

## 发生了什么

- 通过 `claude_tap --tap-client codex` 的常规 Codex 运行经由 HTTP/SSE（`POST /v1/responses`）成功。
- 强制 WS 运行（`--enable responses_websockets` 与 `--enable responses_websockets_v2`）反复连接上游失败：
  - `502 Bad Gateway`
  - 连接 `wss://chatgpt.com/backend-api/codex/responses` 超时
- 多次重试后，Codex 自动回退到 HTTPS 传输并成功完成该轮。

## 根因（观察）

观察到的问题是当前环境中的上游 WS 连接超时，而非 proxy 进程本地崩溃。proxy 正确记录了 WS 失败，client 的 fallback 路径恢复了运行。

## 证据

- `/tmp/pr22-ws-unblock-evidence-20260303/codex-ws-v1-run.log`
- `/tmp/pr22-ws-unblock-evidence-20260303/codex-ws-v2-run.log`
- `/tmp/pr22-ws-unblock-evidence-20260303/codex-ws-v1-run/trace_20260303_180901.jsonl`
- `/tmp/pr22-ws-unblock-evidence-20260303/codex-ws-v2-run/trace_20260303_180901.jsonl`

## 经验

对传输敏感验证，需明确区分三类陈述：
1. 已实现行为（代码 + 测试），
2. 当前环境中的观察行为，
3. 因网络/运行时约束尚未验证的行为。

这样能避免过度声明，同时仍可在已验证范围内推进合并决策。
