---
owner: claude-tap-maintainers
last_reviewed: 2026-05-01
source_of_truth: AGENTS.md
---

# 支持矩阵

本文记录所有已验证的客户端、认证方式、上游目标和传输组合。
**任何代理或路由相关变更在合入前都必须验证适用的矩阵行。**

English version: [Support Matrix](support-matrix.md).

## 客户端配置

| 客户端 | 认证方式 | 上游目标 | strip_path_prefix | 传输 | 状态 |
|--------|----------|----------|-------------------|------|------|
| Claude Code | API Key | `https://api.anthropic.com` | 无 | HTTP/SSE | 已验证 |
| Codex CLI | API Key (`OPENAI_API_KEY`) | `https://api.openai.com` | 无 | HTTP/SSE | 已验证 |
| Codex CLI | API Key (`OPENAI_API_KEY`) | `https://api.openai.com` | 无 | WebSocket | 已验证 |
| Codex CLI | OAuth (`codex login`) | `https://chatgpt.com/backend-api/codex` | `/v1` | HTTP/SSE | 已验证 |
| Codex CLI | OAuth (`codex login`) | `https://chatgpt.com/backend-api/codex` | `/v1` | WebSocket | 已验证 |
| OpenCode | 通过 `opencode providers` 配置 provider 凭据 | Forward proxy（任意 HTTPS 上游） | n/a | HTTP/SSE | 单测覆盖 |
| OpenCode | 仅 Anthropic provider（`--tap-proxy-mode reverse`） | `https://api.anthropic.com` | 无 | HTTP/SSE | 单测覆盖 |
| Cursor CLI | Cursor 登录（`cursor-agent login`） | Forward proxy 到 `https://api2.cursor.sh` | n/a | HTTPS/protobuf + 本地 transcript import | 真实 E2E 已验证 |

## 各客户端默认代理模式

`CLIENT_CONFIGS` 中的每个客户端都会声明一个 `default_proxy_mode`，在未传入 `--tap-proxy-mode` 时使用：

| 客户端 | 默认模式 | 原因 |
|--------|----------|------|
| `claude` | `reverse` | 单 provider，原生支持 `ANTHROPIC_BASE_URL` 环境变量 |
| `codex` | `reverse` | 单 provider，原生支持 `OPENAI_BASE_URL` 环境变量 |
| `opencode` | `forward` | 多 provider；forward proxy 可以捕获所有上游，而不依赖客户端支持哪个环境变量 |
| `cursor` | `forward` | Cursor CLI 没有 base URL 覆盖能力；forward proxy 捕获网络流量，本地 transcript 提供可读对话 |

用户始终可以通过 `--tap-proxy-mode {reverse,forward}` 显式覆盖。

## URL 构造规则

代理按如下方式构造上游 URL：`target + forwarded_path`

如果设置了 `strip_path_prefix`，会先从传入 path 中移除该前缀再转发：

```text
incoming: /v1/responses
strip:    /v1
result:   /responses
upstream: {target}/responses
```

### 决策逻辑

```python
strip = "/v1" if client == "codex" and "api.openai.com" not in target else ""
```

| Target 包含 `api.openai.com` | strip | 示例 |
|------------------------------|-------|------|
| 是 | 无 | `/v1/responses` -> `api.openai.com/v1/responses` |
| 否 | `/v1` | `/v1/responses` -> `chatgpt.com/.../responses` |

## 验证方式

### 自动化验证（CI）

- `test_codex_upstream_url_construction`：验证全部 5 个矩阵组合的 URL 构造
- `test_codex_client_reverse_proxy`：使用 fake upstream 覆盖 OAuth 类 reverse proxy e2e
- `test_websocket_proxy_basic`：验证 WebSocket relay 和 trace 记录
- `test_cursor_registered_in_client_configs`：验证 Cursor CLI 注册和默认 forward 模式
- `test_run_client_cursor_forward_sets_proxy_ca_and_no_proxy`：验证 Cursor forward proxy 启动环境变量
- `test_import_cursor_transcripts_appends_viewer_friendly_records`：验证 Cursor transcript import 会追加 viewer 友好的记录
- `test_import_cursor_transcripts_preserves_tool_uses`：验证 Cursor tool_use block 能在 viewer trace shape 中渲染

### 手动验证（代理变更合入前）

```bash
# API Key 模式
uv run python -m claude_tap --tap-client codex --tap-no-launch --tap-port 0
# 验证日志里的上游 URL 正确

# OAuth 模式
uv run python -m claude_tap --tap-client codex \
  --tap-target https://chatgpt.com/backend-api/codex --tap-no-launch --tap-port 0
# 验证日志里的上游 URL 正确

# Cursor CLI
uv run python -m claude_tap --tap-client cursor -- -p --trust --model auto "Reply OK"
# 验证 trace 同时包含 raw proxy records 和 cursor-transcript records
```

### 真实 E2E（有认证时可选）

```bash
# 基于 tmux 的真实验证
tmux new-session -d -s verify \
  "uv run python -m claude_tap --tap-client codex --tap-target TARGET --tap-no-launch --tap-port 8080"
# 另一个窗口中：
OPENAI_BASE_URL=http://127.0.0.1:8080/v1 codex exec "Reply: OK"
```

```bash
# Cursor CLI 真实验证
uv run python -m claude_tap --tap-client cursor -- -p --trust --model auto \
  "Use tools to inspect the workspace and reply OK"
# 验证生成的 HTML 包含 cursor-transcript turns 和 tool_use blocks。
```

## 添加新客户端或后端

添加新客户端或后端时：

1. 在上方矩阵中新增一行
2. 在 `test_codex_upstream_url_construction` 中增加 URL 构造测试用例
3. 如适用，增加 fake upstream e2e 测试
4. 如果有认证条件，执行真实 E2E 验证
5. 同时更新英文和简体中文公开文档：`README.md` 与 `README_zh.md`，适用时还要更新配对的 `docs/guides/*.md` 与 `docs/guides/*.zh.md`
