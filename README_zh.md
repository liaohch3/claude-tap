# claude-tap

[![PyPI version](https://img.shields.io/pypi/v/claude-tap.svg)](https://pypi.org/project/claude-tap/)
[![PyPI downloads](https://img.shields.io/pypi/dm/claude-tap.svg)](https://pypi.org/project/claude-tap/)
[![Python version](https://img.shields.io/pypi/pyversions/claude-tap.svg)](https://pypi.org/project/claude-tap/)
[![License](https://img.shields.io/github/license/liaohch3/claude-tap.svg)](https://github.com/liaohch3/claude-tap/blob/main/LICENSE)

[English](README.md)

拦截并查看 [Claude Code](https://docs.anthropic.com/en/docs/claude-code)、[Codex CLI](https://github.com/openai/codex)、[OpenCode](https://opencode.ai) 或 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 的所有 API 流量。看清它们如何构造 system prompt、管理对话历史、选择工具、优化 token 用量——通过一个美观的 trace 查看器。

![演示](docs/demo_zh.gif)

![亮色模式](docs/viewer-zh.png)

<details>
<summary>暗色模式 / Diff 视图</summary>

![暗色模式](docs/viewer-dark.png)
![结构化 Diff](docs/diff-modal.png)
![字符级 Diff](docs/billing-header-diff.png)

</details>

## 安装

需要 Python 3.11+ 以及要追踪的客户端：[Claude Code](https://docs.anthropic.com/en/docs/claude-code)（默认）、[Codex CLI](https://github.com/openai/codex)（`--tap-client codex` 时）、[OpenCode](https://opencode.ai)（`--tap-client opencode` 时）、或 [Hermes Agent](https://github.com/NousResearch/hermes-agent)（`--tap-client hermes` 时）。

```bash
# 推荐
uv tool install claude-tap

# 或用 pip
pip install claude-tap
```

升级: `uv tool upgrade claude-tap` 或 `pip install --upgrade claude-tap`

## 使用

### Claude Code

```bash
# 基本用法 — 启动带 trace 的 Claude Code
claude-tap

# 实时模式 — 在浏览器中实时观察 API 调用
claude-tap --tap-live

# 透传参数给 Claude Code
claude-tap -- --model claude-opus-4-6
claude-tap -c    # 继续上次对话

# 跳过所有权限确认（自动批准工具调用）
claude-tap -- --dangerously-skip-permissions

# 全功能组合：实时查看器 + 跳过权限确认 + 指定模型
claude-tap --tap-live -- --dangerously-skip-permissions --model claude-sonnet-4-6
```

### Codex CLI

Codex CLI 支持两种认证方式，对应不同的上游目标：

| 认证方式 | 如何认证 | 上游目标 | 说明 |
|---------|---------|---------|------|
| **OAuth**（ChatGPT 付费套餐） | `codex login` | `https://chatgpt.com/backend-api/codex` | ChatGPT Plus/Pro/Team 用户默认方式 |
| **API Key** | 设置 `OPENAI_API_KEY` | `https://api.openai.com`（默认） | 通过 OpenAI Platform 按量付费 |

`claude-tap` 会尽量根据 Codex 的认证状态自动识别 target。

```bash
# OAuth 用户（ChatGPT Plus/Pro/Team）— `codex login` 后通常会自动识别
claude-tap --tap-client codex

# 如果无法读取 Codex auth 文件，可以显式指定 target
claude-tap --tap-client codex --tap-target https://chatgpt.com/backend-api/codex

# API Key 用户 — 默认 OpenAI API target 即可
claude-tap --tap-client codex

# 指定模型
claude-tap --tap-client codex -- --model codex-mini-latest

# 全自动模式（跳过所有权限确认）
claude-tap --tap-client codex -- --full-auto

# OAuth + 全自动 + 实时查看器
claude-tap --tap-client codex --tap-live -- --full-auto
```

### OpenCode

[OpenCode](https://opencode.ai) 是一款多 provider 的终端 AI 助手。由于它能对接多种 provider，claude-tap 默认对 opencode 使用 **forward proxy** 模式——向子进程注入 `HTTPS_PROXY` 与本地 CA，捕获它对接的任意 provider 流量。

```bash
# forward proxy 模式 — 捕获 opencode 对接的任意 provider（默认）
claude-tap --tap-client opencode

# 配合实时查看器
claude-tap --tap-client opencode --tap-live

# reverse 模式 — 仅在使用 Anthropic provider 时有效（单一 ANTHROPIC_BASE_URL）
claude-tap --tap-client opencode --tap-proxy-mode reverse
```

### Hermes Agent

Hermes Agent 是基于 Python 的多 provider AI agent（Nous Portal / OpenRouter / NVIDIA NIM / 小米 MiMo / GLM / Kimi / MiniMax / Hugging Face / OpenAI / Anthropic / 自定义）。由于它能对接任意 provider，且 `httpx`、`requests` 都默认认 `HTTPS_PROXY` 环境变量，claude-tap 默认对 hermes 使用 **forward proxy** 模式——通过向子进程注入 `HTTPS_PROXY` 与本地 CA，捕获它对接的任意 provider 流量。

Hermes 有两种使用姿势：

```bash
# A) 交互式 TUI — 直接前台跑 hermes，forward 代理透明捕获 LLM 调用。
#    在 TUI 里输入消息，配合 --tap-live 实时看 trace。
claude-tap --tap-client hermes --tap-live

# B) Gateway 模式 — gateway 必须前台跑在 tap 下。claude-tap 会自动把
#    `gateway start`（最近版本会委托给 systemd / launchd）改写为 `gateway run`
#    （前台版），这样新起的 gateway 是我们的子进程，能继承代理环境
claude-tap --tap-client hermes -- gateway start
# 另一个终端 — 用 TUI 连前台的 gateway
hermes

# 反向模式仅在 ~/.hermes 配了一个读 OPENAI_BASE_URL 的 OpenAI 兼容 provider 时才有用
claude-tap --tap-client hermes --tap-proxy-mode reverse
```

### 浏览器预览

```bash
# 禁用退出后自动打开 HTML 查看器（默认开启）
claude-tap --tap-no-open

# 实时模式 — 客户端运行时在浏览器中实时查看
claude-tap --tap-live
claude-tap --tap-live --tap-live-port 3000    # 固定实时查看器端口

# 独立 Dashboard — 不启动客户端，直接浏览历史 trace
claude-tap dashboard
claude-tap dashboard --tap-output-dir ./my-traces --tap-live-port 3000
```

客户端退出后，也可以手动打开生成的查看器：

```bash
open .traces/*/trace_*.html
```

也可以从已有 JSONL trace 重新生成自包含 HTML 查看器：

```bash
claude-tap export .traces/2026-02-28/trace_141557.jsonl -o trace.html
# 或：
claude-tap export .traces/2026-02-28/trace_141557.jsonl --format html
```

### 纯代理模式

仅启动代理，不自动启动客户端 — 适用于自定义场景或在另一个终端手动连接：

```bash
# Claude Code
claude-tap --tap-no-launch --tap-port 8080
# 在另一个终端:
ANTHROPIC_BASE_URL=http://127.0.0.1:8080 claude

# Codex CLI（OAuth）
claude-tap --tap-client codex --tap-target https://chatgpt.com/backend-api/codex --tap-no-launch --tap-port 8080
# 在另一个终端:
OPENAI_BASE_URL=http://127.0.0.1:8080/v1 codex -c 'openai_base_url="http://127.0.0.1:8080/v1"'

# Codex CLI（API Key）
claude-tap --tap-client codex --tap-no-launch --tap-port 8080
# 在另一个终端:
OPENAI_BASE_URL=http://127.0.0.1:8080/v1 codex -c 'openai_base_url="http://127.0.0.1:8080/v1"'
```

### 常用组合

```bash
# 追踪 Claude Code：实时查看器 + 自动批准
claude-tap --tap-live -- --dangerously-skip-permissions

# 追踪 Codex（OAuth）：实时查看器 + 全自动
claude-tap --tap-client codex --tap-target https://chatgpt.com/backend-api/codex --tap-live -- --full-auto

# 自定义 trace 输出目录
claude-tap --tap-output-dir ./my-traces

# 仅保留最近 10 次 trace
claude-tap --tap-max-traces 10
```

### CLI 选项

除以下 `--tap-*` 参数外，所有参数均透传给所选客户端：

```
--tap-client CLIENT      启动的客户端: claude（默认）/ codex / opencode / hermes
--tap-target URL         上游 API 地址（默认: 根据客户端自动选择）
--tap-live               启动实时查看器（自动打开浏览器）
--tap-live-port PORT     实时查看器端口（默认: 自动分配）
--tap-no-open            退出后不自动打开 HTML 查看器（默认开启）
--tap-output-dir DIR     Trace 输出目录（默认: ./.traces）
--tap-port PORT          代理端口（默认: 自动分配）
--tap-host HOST          绑定地址（默认: 127.0.0.1，--tap-no-launch 模式下为 0.0.0.0）
--tap-no-launch          仅启动代理，不启动客户端
--tap-max-traces N       最大保留 trace 数量（默认: 50，0 = 不限）
--tap-no-update-check    禁用启动时的 PyPI 更新检查
--tap-no-auto-update     仅检查更新，不自动下载
--tap-proxy-mode MODE    代理模式: reverse 或 forward（默认：claude/codex 用 reverse，opencode/hermes 用 forward）
```

## 查看器功能

查看器是一个自包含的 HTML 文件（零外部依赖）：

- **结构化 Diff** — 对比相邻请求的变化：新增/删除的消息、system prompt diff、字符级高亮
- **路径过滤** — 按 API 端点筛选（如仅显示 `/v1/messages`）
- **模型分组** — 侧边栏按模型分组，并对 Claude 系列模型做优先排序
- **Token 用量分析** — 输入 / 输出 / 缓存读取 / 缓存创建
- **工具检查器** — 可展开的卡片，显示工具名称、描述和参数 schema
- **全文搜索** — 搜索消息、工具、prompt 和响应
- **暗色模式** — 切换亮色/暗色主题（跟随系统偏好）
- **键盘导航** — `j`/`k` 或方向键
- **复制助手** — 一键复制请求 JSON 或 cURL 命令
- **多语言** — English, 简体中文, 日本語, 한국어, Français, العربية, Deutsch, Русский

## 架构

![架构图](docs/architecture.png)

**工作原理:**

1. `claude-tap` 启动反向代理，并以对应服务商的 base URL 指向代理来启动所选客户端（`claude` 或 `codex`）
2. 支持的 API 请求流经: 代理 → 上游 API → 代理返回
3. SSE 和 WebSocket 流会在收到 chunk/message 时实时转发，代理开销很低
4. 每个请求-响应对或 WebSocket 会话记录到按日期保存的 `trace_*.jsonl`
5. 退出时生成自包含的 HTML 查看器
6. 实时模式（可选）通过 SSE 向浏览器广播更新

**核心特性:** 🔒 常见认证 header 自动脱敏 · ⚡ 低开销流式转发 · 📦 自包含查看器 · 🔄 实时模式

## 社区

[![Star History Chart](https://api.star-history.com/svg?repos=liaohch3/claude-tap&type=Date)](https://www.star-history.com/#liaohch3/claude-tap&Date)

<a href="https://github.com/liaohch3/claude-tap/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=liaohch3/claude-tap" alt="贡献者" />
</a>

## 许可证

MIT
