# claude-tap

[![PyPI version](https://img.shields.io/pypi/v/claude-tap.svg)](https://pypi.org/project/claude-tap/)
[![PyPI downloads](https://img.shields.io/pypi/dm/claude-tap.svg)](https://pypi.org/project/claude-tap/)
[![Python version](https://img.shields.io/pypi/pyversions/claude-tap.svg)](https://pypi.org/project/claude-tap/)
[![License](https://img.shields.io/github/license/liaohch3/claude-tap.svg)](https://github.com/liaohch3/claude-tap/blob/main/LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/liaohch3/claude-tap?style=social)](https://github.com/liaohch3/claude-tap/stargazers)
[![All Contributors](https://img.shields.io/badge/all_contributors-9-orange.svg)](#贡献者)

[English](README.md)

`claude-tap` 是给 AI 编程 agent 用的本地代理和 trace 查看器。把 CLI 通过它启动，就能看到真实 API 流量：system prompt、对话历史、工具 schema、工具调用、流式响应、token 用量和请求 diff。

它支持 [Claude Code](https://docs.anthropic.com/en/docs/claude-code)、[Codex CLI](https://github.com/openai/codex)、[Gemini CLI](https://github.com/google-gemini/gemini-cli)、[Kimi CLI](https://github.com/MoonshotAI/kimi-cli)、[OpenCode](https://opencode.ai)、[Pi](https://github.com/badlogic/pi-mono/tree/main/packages/coding-agent)、[Hermes Agent](https://github.com/NousResearch/hermes-agent)、[Cursor CLI](https://cursor.com/cli)、[Qoder CLI](https://qoder.com/cli)、[Antigravity CLI](https://antigravity.google/product/antigravity-cli) 和 [CodeBuddy CLI](https://www.codebuddy.ai)。

<p align="center">
  <img src="docs/demo_zh.gif" alt="claude-tap 演示：真实 Codex trace" width="100%">
  <br>
  <sub>打开一次真实 agent 运行，检查每个请求，并对比上下文如何在多轮之间变化。</sub>
</p>

<table>
  <tr>
    <td width="33%" align="center">
      <img src="docs/viewer-zh.png" alt="亮色模式 trace 查看器" width="100%">
      <br>
      <sub>亮色模式总览</sub>
    </td>
    <td width="33%" align="center">
      <img src="docs/viewer-dark.png" alt="暗色模式 trace 查看器" width="100%">
      <br>
      <sub>适合长时间 review 的暗色模式</sub>
    </td>
    <td width="33%" align="center">
      <img src="docs/diff-modal.png" alt="结构化 Diff 弹窗" width="100%">
      <br>
      <sub>相邻请求之间的结构化 Diff</sub>
    </td>
  </tr>
</table>

## 为什么用它

- 👀 **看见真实上下文**：检查 prompt、messages、工具定义、工具调用、工具结果、流式 chunk 和 token 用量。
- 🔎 **用证据定位问题**：对比相邻请求，明确是哪段 prompt、消息、工具或参数发生了变化。
- 📦 **留下可分享证据**：每次运行都会写入 JSONL trace，并生成自包含 HTML 查看器，方便 review 或归档。
- 🔒 **数据留在本机**：不依赖云端 dashboard；常见认证 header 会在记录前自动脱敏。
- 🧩 **覆盖主流编码 CLI**：同一套流程可用于 Claude Code、Codex CLI、Gemini CLI、Kimi CLI、OpenCode、Pi、Hermes Agent、Cursor CLI、Qoder CLI、Antigravity CLI 和 CodeBuddy CLI。

## 支持的客户端

| 客户端 | 典型用途 |
|--------|----------|
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | Anthropic API，或 DeepSeek / GLM 等 Claude 兼容网关 |
| [Codex CLI](https://github.com/openai/codex) | OpenAI API 密钥模式，或 ChatGPT 订阅 OAuth |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli) | Google OAuth / Code Assist 的多 Google 端点流量 |
| [Kimi CLI](https://github.com/MoonshotAI/kimi-cli) | Kimi Code 或 Moonshot Open Platform |
| [OpenCode](https://opencode.ai) | 多提供方 OpenCode 会话 |
| [Pi](https://github.com/badlogic/pi-mono/tree/main/packages/coding-agent) | Pi 会话，包括 OpenAI Codex OAuth 提供方 |
| [Hermes Agent](https://github.com/NousResearch/hermes-agent) | 多提供方 Hermes TUI 或 gateway 会话 |
| [Cursor CLI](https://cursor.com/cli) | Cursor Agent 会话，并导入可读的本地 transcript |
| [Qoder CLI](https://qoder.com/cli) | 通过 forward proxy 捕获 Qoder Agent 会话 |
| [Antigravity CLI](https://antigravity.google/product/antigravity-cli) | 通过 forward proxy 捕获 Antigravity Agent 会话 |
| [CodeBuddy CLI](https://www.codebuddy.ai) | 腾讯 CodeBuddy SaaS 或内部 Copilot 端点 |

## 安装

需要 Python 3.11+，以及你要追踪的客户端。

```bash
# 推荐
uv tool install claude-tap

# 或用 pip
pip install claude-tap
```

升级: `claude-tap update`、`uv tool upgrade claude-tap` 或 `pip install --upgrade claude-tap`

## 快速开始

用 `claude-tap` 启动你想观察的客户端。`--` 后面的参数会透传给所选客户端。

```bash
# Claude Code，默认开启浏览器实时查看器
claude-tap

# 恢复 v0.1.75 之前的行为：不启动实时查看器
claude-tap --tap-no-live

# Codex CLI
claude-tap --tap-client codex

# Gemini CLI
claude-tap --tap-client gemini -- -p "hello"

# Kimi CLI
claude-tap --tap-client kimi

# Pi
claude-tap --tap-client pi -- --model openai-codex/gpt-5.3-codex-spark -p "hello"

# Cursor CLI
claude-tap --tap-client cursor -- -p --trust --model auto "hello"

# Qoder CLI
claude-tap --tap-client qoder -- -p "hello" --permission-mode dont_ask

# Antigravity CLI
claude-tap --tap-client agy

# CodeBuddy CLI
claude-tap --tap-client codebuddy
```

<details>
<summary>Claude Code 更多示例</summary>

```bash
# 透传参数给 Claude Code
claude-tap -- --model claude-opus-4-6
claude-tap -c    # 继续上次对话

# 跳过所有权限确认（自动批准工具调用）
claude-tap -- --dangerously-skip-permissions

# 实时查看器默认开启；-- 后面的参数透传给 Claude Code
claude-tap -- --dangerously-skip-permissions --model claude-sonnet-4-6
```

`claude-tap` 会从环境变量或 Claude settings 中的 `ANTHROPIC_BASE_URL`
自动识别自定义 Claude Code 上游；只有想手动覆盖时才需要传 `--tap-target`。

使用 Claude Code VS Code 插件时，把 `Claude Code: Claude Process Wrapper` 设置为 `claude-tap`；如果 Windows 上 VS Code 找不到它，请填写完整的 `claude-tap.exe` 路径。

</details>

<details>
<summary>Claude Code + DeepSeek API</summary>

完整中文指南见 [Claude Code 搭配 DeepSeek API](docs/guides/deepseek-claude-code.zh.md)，英文版见 [Claude Code with DeepSeek API](docs/guides/deepseek-claude-code.md)。

```bash
export ANTHROPIC_AUTH_TOKEN="<你的 DeepSeek API key>"
unset ANTHROPIC_API_KEY

export ANTHROPIC_MODEL="deepseek-v4-pro[1m]"
export ANTHROPIC_DEFAULT_OPUS_MODEL="deepseek-v4-pro[1m]"
export ANTHROPIC_DEFAULT_SONNET_MODEL="deepseek-v4-pro[1m]"
export ANTHROPIC_DEFAULT_HAIKU_MODEL="deepseek-v4-flash"
export CLAUDE_CODE_SUBAGENT_MODEL="deepseek-v4-flash"
export CLAUDE_CODE_EFFORT_LEVEL=max
export ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
```

```bash
claude-tap -- --permission-mode bypassPermissions
```

`claude-tap` 会从 `ANTHROPIC_BASE_URL` 读取 DeepSeek 上游，再把 Claude Code 指向本地代理。只有手动覆盖时才需要 `--tap-target https://api.deepseek.com/anthropic`。

</details>

<details>
<summary>Codex CLI 认证方式和示例</summary>

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

# OAuth + 全自动；实时查看器默认开启
claude-tap --tap-client codex -- --full-auto
```

</details>

<details>
<summary>Kimi CLI 示例</summary>

Kimi CLI 默认通过 `KIMI_BASE_URL` 使用 reverse proxy。使用你已有的 Kimi CLI 认证和配置；默认上游目标是 Kimi Code API。

```bash
claude-tap --tap-client kimi
claude-tap --tap-client kimi -- --thinking

# 改用 Moonshot Open Platform，而不是 Kimi Code
claude-tap --tap-client kimi --tap-target https://api.moonshot.ai/v1
```

</details>

<details>
<summary>Gemini CLI 示例</summary>

Gemini CLI 默认使用 forward proxy。Google OAuth / Code Assist 流量会访问多个 Google 端点，因此 forward proxy 是更稳妥的默认抓取方式。对于会读取 `GOOGLE_GEMINI_BASE_URL` 或 `GOOGLE_VERTEX_BASE_URL` 的 API key / Vertex 类流程，仍可显式使用 reverse 模式。

```bash
# Google OAuth / Code Assist
claude-tap --tap-client gemini -- -p "hello"

# 实时查看器默认开启
claude-tap --tap-client gemini -- -p "hello"

# API key / Vertex 兼容流程的 reverse 模式
claude-tap --tap-client gemini --tap-proxy-mode reverse -- -p "hello"
```

</details>

<details>
<summary>OpenCode 示例</summary>

[OpenCode](https://opencode.ai) 是一款多 provider 的终端 AI 助手。由于它能对接多种 provider，claude-tap 默认对 opencode 使用 **forward proxy** 模式——向子进程注入 `HTTPS_PROXY` 与本地 CA，捕获它对接的任意 provider 流量。

```bash
# forward proxy 模式 — 捕获 opencode 对接的任意 provider（默认）
claude-tap --tap-client opencode

# 实时查看器默认开启
claude-tap --tap-client opencode

# reverse 模式 — 仅在使用 Anthropic provider 时有效（单一 ANTHROPIC_BASE_URL）
claude-tap --tap-client opencode --tap-proxy-mode reverse
```

</details>

<details>
<summary>Pi 示例</summary>

[Pi](https://github.com/badlogic/pi-mono/tree/main/packages/coding-agent) 是一个多 provider coding agent。因为 Pi 可以使用 `openai-codex` 这类订阅 OAuth provider，也可以使用模型注册表中的自定义 API-key provider，claude-tap 默认对 Pi 使用 **forward proxy** 模式。

```bash
# 通过 Pi 的 openai-codex provider 使用 OpenAI Codex OAuth
claude-tap --tap-client pi -- --model openai-codex/gpt-5.3-codex-spark -p "hello"

# 实时查看器默认开启
claude-tap --tap-client pi -- --model openai-codex/gpt-5.3-codex-spark -p "hello"

# 捕获只读工具调用
claude-tap --tap-client pi -- --model openai-codex/gpt-5.3-codex-spark --tools bash -p "Run pwd"
```

Pi 在 `/login` 后会把 OAuth 凭据保存在 `~/.pi/agent/auth.json`。如果你把 Pi 凭据放在其他目录，请在启动 `claude-tap` 前设置 `PI_CODING_AGENT_DIR`。

</details>

<details>
<summary>Hermes Agent 示例</summary>

Hermes Agent 是基于 Python 的多 provider AI agent（Nous Portal / OpenRouter / NVIDIA NIM / 小米 MiMo / GLM / Kimi / MiniMax / Hugging Face / OpenAI / Anthropic / 自定义）。由于它能对接任意 provider，且 `httpx`、`requests` 都默认认 `HTTPS_PROXY` 环境变量，claude-tap 默认对 hermes 使用 **forward proxy** 模式——通过向子进程注入 `HTTPS_PROXY` 与本地 CA，捕获它对接的任意 provider 流量。

```bash
# 交互式 TUI — 本地抓 trace 的推荐方式。
claude-tap --tap-client hermes

# Gateway 模式 — 捕获由 Slack、Telegram 等平台消息触发的 LLM 调用。
# 需要在 ~/.hermes/.env 中配置消息平台。
# claude-tap 自动将 `gateway start` 改写为 `gateway run`，使 gateway 在前台运行并
# 继承 HTTPS_PROXY；否则 systemd/launchd 启动的守护进程不会经过代理，无法抓到 trace。
claude-tap --tap-client hermes -- gateway start

# 反向模式仅在 ~/.hermes 配了一个读 OPENAI_BASE_URL 的 OpenAI 兼容 provider 时才有用
claude-tap --tap-client hermes --tap-proxy-mode reverse
```

> **注意：** Gateway 模式只有在配置的消息平台（Slack、Telegram 等）推送消息给 bot 时才会产生 trace。若没有活跃的平台集成，gateway 不会发起 LLM 请求，也不会生成任何 trace。

</details>

<details>
<summary>Cursor CLI 示例</summary>

Cursor CLI 默认使用 forward proxy。免费套餐建议传 `--model auto`；需要工具调用时不要加 `--mode ask`。

```bash
claude-tap --tap-client cursor -- -p --trust --model auto "hello"
claude-tap --tap-client cursor -- -p --trust --model auto --continue "continue"
```

</details>

## 集成与指南

- [OpenClaw 设置指南](docs/guides/OPENCLAW_README.zh.md)：在 OpenClaw 中集成 `claude-tap`。英文版见 [OpenClaw setup guide](docs/guides/OPENCLAW_README.md)。
- [Claude Code 搭配 DeepSeek API](docs/guides/deepseek-claude-code.zh.md)：让 Claude Code 走 DeepSeek 的 Anthropic 兼容 API。英文版见 [Claude Code with DeepSeek API](docs/guides/deepseek-claude-code.md)。
- [客户端支持矩阵](docs/support-matrix.md)：查看各客户端对应的环境变量、代理模式和 URL 改写规则。

<details>
<summary>Qoder CLI 示例</summary>

Qoder CLI 会访问多个 Qoder 端点，因此 `--tap-client qoder` 默认使用 **forward proxy** 模式。

```bash
# 启动前需要先配置浏览器登录、PAT 或 job token。
qodercli login

claude-tap --tap-client qoder -- -p "hello" --permission-mode dont_ask
```

</details>

<details>
<summary>Antigravity CLI 示例</summary>

Antigravity CLI 会访问多个 Google / Antigravity 端点，因此 `--tap-client agy` 默认使用 **forward proxy** 模式。它的 Code Assist 模型 API 还会读取 `CLOUD_CODE_URL`；claude-tap 会自动注入这个变量，让 `/v1internal:streamGenerateContent` 这类模型请求也进入同一个本地代理。

在 macOS 上，Antigravity 可能不读取进程级 CA 环境变量。首次启动 `agy` 时，claude-tap 会自动把本地 CA 信任到当前用户的 login keychain。这个操作不会使用 `sudo`，也不会写入 System keychain，但 macOS 可能要求解锁 login keychain。

```bash
claude-tap --tap-client agy --tap-live

# 可选：也可以先单独信任 CA，再启动 forward proxy 客户端。
claude-tap trust-ca
```

</details>

<details>
<summary>CodeBuddy CLI 示例</summary>

CodeBuddy 默认使用 reverse proxy。claude-tap 会自动从 CodeBuddy 自己的登录缓存（`~/.codebuddy/local_storage/`）识别上游地址，所以 iOA / WeChat / Google-Github / Enterprise-Domain 四种登录方式登录后都可以零参数启动。当缓存还不存在（例如首次登录前）时，会回退到 `https://copilot.tencent.com/v2`。

```bash
# 自动识别上游（登录后四种登录方式都适用）
claude-tap --tap-client codebuddy

# 显式指定上游（外网 SaaS 或 staging）
claude-tap --tap-client codebuddy --tap-target https://www.codebuddy.ai/v2

# 或通过环境变量
CODEBUDDY_BASE_URL=https://www.codebuddy.ai/v2 claude-tap --tap-client codebuddy -- -p "Reply OK"
```

</details>

<details>
<summary>查看器、导出和高级选项</summary>

```bash
# 客户端运行时默认启动实时查看器
claude-tap

# 脚本、CI、远程 shell 或需要旧行为时关闭实时查看器
claude-tap --tap-no-live

# 不启动客户端，直接浏览历史 trace
claude-tap dashboard

# 从已有 JSONL trace 重新生成自包含 HTML 查看器
claude-tap export .traces/2026-02-28/trace_141557.jsonl -o trace.html

# 自定义 trace 输出目录，或限制保留数量
claude-tap --tap-output-dir ./my-traces
claude-tap --tap-max-traces 10

# 只启动代理，给自定义场景使用
claude-tap --tap-no-launch --tap-port 8080

# 不自动在浏览器里打开实时或生成的查看器
claude-tap --tap-no-open
```

纯代理模式下，可以在另一个终端启动客户端，并把它的 base URL 或代理配置指向本地代理。具体接法见 [客户端支持矩阵](docs/support-matrix.md)。

### CLI 选项

除以下 `--tap-*` 参数外，所有参数均透传给所选客户端：

```
--tap-client CLIENT      启动的客户端: claude（默认）/ agy / codex / gemini / kimi / opencode / pi / hermes / cursor / qoder / codebuddy
--tap-target URL         上游 API 地址（默认: 根据客户端自动选择）
--tap-live               客户端运行时启动实时查看器（默认开启）
--tap-no-live            关闭实时查看器（恢复 v0.1.75 之前的行为）
--tap-live-port PORT     实时查看器端口（默认: 自动分配）
--tap-no-open            不自动在浏览器里打开实时或生成的 HTML 查看器
--tap-output-dir DIR     Trace 输出目录（默认: ./.traces）
--tap-port PORT          代理端口（默认: 自动分配）
--tap-host HOST          绑定地址（默认: 127.0.0.1，--tap-no-launch 模式下为 0.0.0.0）
--tap-no-launch          仅启动代理，不启动客户端
--tap-max-traces N       最大保留 trace 数量（默认: 50，0 = 不限）
--tap-no-update-check    禁用启动时的 PyPI 更新检查
--tap-no-auto-update     仅检查更新，不自动下载
--tap-proxy-mode MODE    代理模式: reverse 或 forward（默认：claude/codex/kimi/codebuddy 用 reverse，agy/gemini/opencode/pi/hermes/cursor/qoder 用 forward）
--tap-trust-ca           macOS 上显式把本地 CA 信任到当前用户 login keychain（agy 会自动执行）
```

</details>

## 查看器功能

### Trace 查看器能力

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

<details>
<summary>工作原理</summary>

**工作原理:**

1. `claude-tap` 启动反向代理或 forward proxy，并启动所选客户端
2. 支持 base URL 的客户端会指向反向代理；不支持 base URL 的客户端会通过 proxy/CA 环境变量接入
3. SSE 和 WebSocket 流会在收到 chunk/message 时实时转发，代理开销很低
4. 每个请求-响应对或 WebSocket 会话记录到按日期保存的 `trace_*.jsonl`
5. 退出时生成自包含的 HTML 查看器
6. 实时模式默认开启，并通过 SSE 向浏览器广播更新

**核心特性:** 🔒 常见认证 header 自动脱敏 · ⚡ 低开销流式转发 · 📦 自包含查看器 · 🔄 实时模式

</details>

## 社区

### Star 历史

<a href="https://www.star-history.com/?repos=liaohch3%2Fclaude-tap&type=date&legend=bottom-right">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=liaohch3/claude-tap&type=date&theme=dark&legend=top-left" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=liaohch3/claude-tap&type=date&legend=top-left" />
    <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=liaohch3/claude-tap&type=date&legend=top-left" />
  </picture>
</a>

### 贡献者

感谢以下贡献者：

<!-- ALL-CONTRIBUTORS-LIST:START - Do not remove or modify this section -->
<!-- prettier-ignore-start -->
<!-- markdownlint-disable -->
<table>
  <tbody>
    <tr>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/liaohch3"><img src="https://avatars.githubusercontent.com/u/34056481?s=100" width="100px;" alt="liaohch3"/><br /><sub><b>liaohch3</b></sub></a><br /><a href="https://github.com/liaohch3/claude-tap/commits?author=liaohch3" title="Code">💻</a> <a href="https://github.com/liaohch3/claude-tap/commits?author=liaohch3" title="Documentation">📖</a> <a href="#maintenance-liaohch3" title="Maintenance">🚧</a> <a href="https://github.com/liaohch3/claude-tap/commits?author=liaohch3" title="Tests">⚠️</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/WEIFENG2333"><img src="https://avatars.githubusercontent.com/u/61730227?s=100" width="100px;" alt="BKK"/><br /><sub><b>BKK</b></sub></a><br /><a href="https://github.com/liaohch3/claude-tap/commits?author=WEIFENG2333" title="Code">💻</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/YoungCan-Wang"><img src="https://avatars.githubusercontent.com/u/73347006?s=100" width="100px;" alt="YoungCan-Wang"/><br /><sub><b>YoungCan-Wang</b></sub></a><br /><a href="https://github.com/liaohch3/claude-tap/commits?author=YoungCan-Wang" title="Code">💻</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/oxkrypton"><img src="https://avatars.githubusercontent.com/u/154910746?s=100" width="100px;" alt="0xkrypton"/><br /><sub><b>0xkrypton</b></sub></a><br /><a href="https://github.com/liaohch3/claude-tap/commits?author=oxkrypton" title="Code">💻</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/googs1025"><img src="https://avatars.githubusercontent.com/u/86391540?s=100" width="100px;" alt="CYJiang"/><br /><sub><b>CYJiang</b></sub></a><br /><a href="https://github.com/liaohch3/claude-tap/commits?author=googs1025" title="Code">💻</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/TITOCHAN2023"><img src="https://avatars.githubusercontent.com/u/138754853?s=100" width="100px;" alt="陈展鹏"/><br /><sub><b>陈展鹏</b></sub></a><br /><a href="https://github.com/liaohch3/claude-tap/commits?author=TITOCHAN2023" title="Documentation">📖</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/devtalker"><img src="https://avatars.githubusercontent.com/u/23204195?s=100" width="100px;" alt="devtalker"/><br /><sub><b>devtalker</b></sub></a><br /><a href="https://github.com/liaohch3/claude-tap/commits?author=devtalker" title="Code">💻</a></td>
    </tr>
    <tr>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/dingyaguang117"><img src="https://avatars.githubusercontent.com/u/1930778?s=100" width="100px;" alt="Yaguang Ding"/><br /><sub><b>Yaguang Ding</b></sub></a><br /><a href="https://github.com/liaohch3/claude-tap/commits?author=dingyaguang117" title="Code">💻</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/sephymartin"><img src="https://avatars.githubusercontent.com/u/299891?s=100" width="100px;" alt="Sephy"/><br /><sub><b>Sephy</b></sub></a><br /><a href="https://github.com/liaohch3/claude-tap/commits?author=sephymartin" title="Code">💻</a></td>
    </tr>
  </tbody>
</table>

<!-- markdownlint-restore -->
<!-- prettier-ignore-end -->

<!-- ALL-CONTRIBUTORS-LIST:END -->

## 许可证

MIT
