---
owner: claude-tap-maintainers
last_reviewed: 2026-05-20
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
| Claude Code | Claude 兼容网关（`ANTHROPIC_BASE_URL` 环境变量或 Claude settings） | 自定义 Anthropic 兼容上游 | 无 | HTTP/SSE | 单测覆盖；DeepSeek 真实 E2E 已验证 |
| Codex CLI | API Key (`OPENAI_API_KEY`) | `https://api.openai.com` | 无 | HTTP/SSE | 已验证 |
| Codex CLI | API Key (`OPENAI_API_KEY`) | `https://api.openai.com` | 无 | WebSocket | 已验证 |
| Codex CLI | OAuth (`codex login`) | `https://chatgpt.com/backend-api/codex` | `/v1` | HTTP/SSE | 已验证 |
| Codex CLI | OAuth (`codex login`) | `https://chatgpt.com/backend-api/codex` | `/v1` | WebSocket | 已验证 |
| Gemini CLI | Google OAuth / Code Assist | Forward proxy（Google 端点） | n/a | HTTP/SSE | 真实 E2E 已验证 |
| Gemini CLI | API key / Vertex 兼容配置（`--tap-proxy-mode reverse`） | `https://generativelanguage.googleapis.com` | 无 | HTTP/SSE | 单测覆盖 |
| Kimi CLI | Kimi CLI 认证/配置 | `https://api.kimi.com/coding/v1` | 无 | HTTP/SSE Chat Completions | 单测覆盖 |
| Kimi CLI | Kimi CLI 认证/配置 | `https://api.moonshot.ai/v1` | 无 | HTTP/SSE Chat Completions | 配置支持 |
| OpenCode | 通过 `opencode providers` 配置 provider 凭据（OpenAI OAuth 与 OpenCode free provider 均已验证） | Forward proxy（任意 HTTPS 上游） | n/a | HTTP/SSE | 真实 E2E 已验证 |
| OpenCode | 仅 Anthropic provider（`--tap-proxy-mode reverse`） | `https://api.anthropic.com` | 无 | HTTP/SSE | 单测覆盖 |
| Pi | 通过 Pi `/login` 或 `PI_CODING_AGENT_DIR` auth 文件配置 provider 凭据（`openai-codex` OAuth 已验证） | Forward proxy（任意 HTTPS 上游） | n/a | HTTP/SSE + WebSocket | 真实 E2E 已验证 |
| Pi | 自定义 OpenAI 兼容配置（`--tap-proxy-mode reverse`） | `https://api.openai.com` | 无 | HTTP/SSE | 单测覆盖 |
| Hermes Agent | 通过 `~/.hermes/` 配置 provider 凭据 | Forward proxy（任意 HTTPS 上游） | n/a | HTTP/SSE | 单测覆盖 |
| Hermes Agent | 自定义 OpenAI 兼容 provider（`--tap-proxy-mode reverse`） | `https://api.openai.com` | `/v1` | HTTP/SSE | 单测覆盖 |
| Cursor CLI | Cursor 登录（`cursor-agent login`） | Forward proxy 到 `https://api2.cursor.sh` | n/a | HTTPS/protobuf + 本地 transcript import | 真实 E2E 已验证 |
| Qoder CLI | Qoder 登录 / `QODER_PERSONAL_ACCESS_TOKEN` / `QODER_JOB_TOKEN` | Forward proxy（Qoder 端点） | n/a | HTTP/SSE | 真实 E2E 已验证 |
| Antigravity CLI | Antigravity 登录 | Forward proxy + `CLOUD_CODE_URL` bridge 到 `https://daily-cloudcode-pa.googleapis.com` | `CLOUD_CODE_URL` | HTTP/SSE | 手动 E2E 已验证；启动环境、Code Assist bridge 和 macOS 用户 keychain CA 自动信任已由单测覆盖 |
| CodeBuddy CLI | CodeBuddy 登录（iOA / WeChat / Google-Github / Enterprise Domain） | 自动从 `~/.codebuddy/local_storage/` 缓存识别；默认 `https://copilot.tencent.com/v2` | `CODEBUDDY_BASE_URL` | HTTP/SSE Chat Completions | iOA 真实 E2E 已验证 |

## 各客户端默认代理模式

`CLIENT_CONFIGS` 中的每个客户端都会声明一个 `default_proxy_mode`，在未传入 `--tap-proxy-mode` 时使用：

| 客户端 | 默认模式 | 原因 |
|--------|----------|------|
| `claude` | `reverse` | 单 provider，原生支持 `ANTHROPIC_BASE_URL` 环境变量 |
| `codex` | `reverse` | 单 provider，原生支持 `OPENAI_BASE_URL` 环境变量 |
| `gemini` | `forward` | Google OAuth / Code Assist 会访问多个 Google 端点；forward proxy 不依赖单一 base URL，更适合作为默认 |
| `kimi` | `reverse` | 单 provider，原生支持 `KIMI_BASE_URL` 环境变量 |
| `opencode` | `forward` | 多 provider；forward proxy 可以捕获所有上游，而不依赖客户端支持哪个环境变量 |
| `pi` | `forward` | 多 provider；Pi 可以使用 OpenAI Codex OAuth 和自定义 model registry provider，forward proxy 不依赖单一 base URL 覆盖即可捕获流量 |
| `hermes` | `forward` | 多 provider 的 Python agent；`httpx` 与 `requests` 都原生认 `HTTPS_PROXY`，forward proxy 捕获是最自然的默认 |
| `cursor` | `forward` | Cursor CLI 没有 base URL 覆盖能力；forward proxy 捕获网络流量，本地 transcript 提供可读对话 |
| `qoder` | `forward` | Qoder CLI 会访问多个 Qoder 服务端点，且没有可靠的单一 base URL 覆盖能力 |
| `agy` | `forward` | Antigravity 会访问多个 Google / Antigravity 端点；claude-tap 用 `HTTPS_PROXY` 捕获辅助流量，并用 `CLOUD_CODE_URL` 捕获 Code Assist 模型流量 |
| `codebuddy` | `reverse` | 单 provider，原生支持 `CODEBUDDY_BASE_URL` 环境变量；支持 `--settings` 环境变量注入；上游 endpoint 自动从 CodeBuddy 登录缓存识别 |

用户始终可以通过 `--tap-proxy-mode {reverse,forward}` 显式覆盖。

## 子命令 argv 改写

部分客户端会把长期运行的守护进程委托给操作系统服务管理器（launchd / systemd / schtasks）。被托管派生出的守护进程**不会**继承我们注入的代理 / CA 环境，trace 抓取会静默失败。claude-tap 会检测这些模式并把 argv 改写为前台等价命令：

| 客户端 | 命中的 argv | 改写为 | 原因 |
|--------|-------------|--------|------|
| `hermes` | `gateway start [...]` | `gateway run [...]` | 较新版本的 hermes 会把 `gateway start` 委托给 systemd / launchd；`gateway run` 是前台等价命令，也正是 systemd unit 的 `ExecStart=` 自身实际执行的命令 |

进程启动时会显式打印改写日志，方便用户察觉。如果用户确实需要守护化行为（并接受抓不到流量），可以加 `--tap-no-launch` 自行运行原命令。

> **注意：** Gateway 模式只有在配置的消息平台（Slack、Telegram 等）推送消息给 bot 时才会产生 trace。
> 若没有活跃的平台集成，gateway 不会发起 LLM 请求，也不会生成任何 trace。
> 本地抓 trace 请使用 TUI 模式（`claude-tap --tap-client hermes`）。

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
strip = CLIENT_CONFIGS[client].reverse_strip_path_prefix(target)
```

| Target 包含 `api.openai.com` | strip | 示例 |
|------------------------------|-------|------|
| 是 | 无 | `/v1/responses` -> `api.openai.com/v1/responses` |
| 否 | `/v1` | `/v1/responses` -> `chatgpt.com/.../responses` |

## 验证方式

### 自动化验证（CI）

- `test_codex_upstream_url_construction`：验证全部 5 个矩阵组合的 URL 构造
- `test_codex_client_reverse_proxy`：使用 fake upstream 覆盖 OAuth 类 reverse proxy e2e
- `test_gemini_registered_in_client_configs`：验证 Gemini CLI 注册和默认 forward 模式
- `test_run_client_gemini_forward_sets_proxy_ca_and_skips_base_url_envs`：验证 Gemini forward proxy 启动环境变量
- `test_run_client_gemini_reverse_sets_both_base_url_envs`：验证 Gemini reverse proxy base URL 环境变量注入
- `test_viewer_renders_gemini_semantic_sections`：验证 Gemini systemInstruction、contents、functionDeclarations、functionCall、functionResponse、SSE output 和 token usage 会渲染为语义化 viewer 区块
- `test_kimi_registered_in_client_configs`：验证 Kimi CLI 注册
- `test_kimi_client_reverse_proxy`：使用 fake Kimi Chat Completions stream 覆盖 e2e
- `test_chat_completions_reasoning_content_is_mirrored_as_thinking`：验证 Kimi thinking stream 渲染形状
- `test_websocket_proxy_basic`：验证 WebSocket relay 和 trace 记录
- `test_hermes_*`：验证 Hermes 注册、parse_args 默认模式解析、forward/reverse 启动环境、argv 改写
- `test_pi_*`：验证 Pi 注册、parse_args 默认模式解析、forward/reverse 启动环境和参数透传
- `test_cursor_registered_in_client_configs`：验证 Cursor CLI 注册和默认 forward 模式
- `test_run_client_cursor_forward_sets_proxy_ca_and_no_proxy`：验证 Cursor forward proxy 启动环境变量
- `test_import_cursor_transcripts_appends_viewer_friendly_records`：验证 Cursor transcript import 会追加 viewer 友好的记录
- `test_import_cursor_transcripts_preserves_tool_uses`：验证 Cursor tool_use block 能在 viewer trace shape 中渲染
- `test_qoder_*`：验证 Qoder 注册、parse_args 默认模式解析、forward/reverse 环境变量和参数透传
- `test_parse_args_agy_does_not_require_tap_trust_ca`：验证 Antigravity 使用和其他客户端一致的启动形态
- `test_auto_ca_trust_*`：验证 Antigravity 会自动请求 macOS 用户 keychain CA 信任，且不需要 sudo
- `test_macos_*_ca_command_*`：验证 CA 信任命令使用用户 login keychain 且不会调用 sudo
- `test_codebuddy_*`：验证 CodeBuddy 注册、parse_args 默认 reverse 模式、settings 注入、forward/reverse 环境变量、`CODEBUDDY_BASE_URL` 探测，以及登录缓存读取

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

# Qoder CLI
uv run python -m claude_tap --tap-client qoder -- -p "Reply OK" --permission-mode dont_ask
# 验证 stdout 包含 assistant 响应，trace 包含 Qoder 端点记录

# Antigravity CLI（macOS）
uv run python -m claude_tap --tap-client agy --tap-live
# 首次运行时，验证 macOS 只要求解锁用户 login keychain，不要求 sudo/admin 写 System keychain。
# 然后验证 trace 包含 /v1internal:streamGenerateContent 模型记录。

# Kimi CLI
uv run python -m claude_tap --tap-client kimi -- --thinking
# 验证 trace 包含 /chat/completions 记录和 thinking/text 输出

# Gemini CLI
uv run python -m claude_tap --tap-client gemini -- -p "Reply OK" --yolo --output-format text
# 验证 trace 包含 Google OAuth / Code Assist API 记录

# Pi
uv run python -m claude_tap --tap-client pi -- \
  --model openai-codex/gpt-5.3-codex-spark -p "Reply OK"
# 验证 trace 包含 chatgpt.com/backend-api 记录和可读的 OpenAI Responses 区块

# CodeBuddy（登录后自动识别端点）
uv run python -m claude_tap --tap-client codebuddy -- -p "Reply OK"
# 验证 trace 包含 /v2/chat/completions 记录,且响应有非零 token 计数
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

```bash
# Gemini CLI 真实验证
uv run python -m claude_tap --tap-client gemini -- -p \
  "Use tools to inspect the workspace and reply OK" --yolo --output-format text
# 验证 trace 包含 cloudcode-pa.googleapis.com / streamGenerateContent 记录。
```

```bash
# Pi + OpenAI Codex OAuth 真实验证
uv run python -m claude_tap --tap-client pi -- \
  --model openai-codex/gpt-5.3-codex-spark --tools bash -p \
  "Use bash to inspect the workspace and reply OK"
# 验证生成的 viewer 展示 Tools、System Prompt、Messages、Response、
# SSE/WebSocket events、工具调用、工具输出和 token usage。
```

## 添加新客户端或后端

添加新客户端或后端时：

1. 在上方矩阵中新增一行
2. 增加 `CLIENT_CONFIGS` 条目和启动/配置测试
3. 如适用，增加 fake upstream e2e 测试
4. 如果有认证条件，执行真实 E2E 验证
5. 同时更新英文和简体中文公开文档：`README.md` 与 `README_zh.md`，适用时还要更新配对的 `docs/guides/*.md` 与 `docs/guides/*.zh.md`
