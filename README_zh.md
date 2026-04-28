# claude-tap

[![PyPI version](https://img.shields.io/pypi/v/claude-tap.svg)](https://pypi.org/project/claude-tap/)
[![PyPI downloads](https://img.shields.io/pypi/dm/claude-tap.svg)](https://pypi.org/project/claude-tap/)
[![Python version](https://img.shields.io/pypi/pyversions/claude-tap.svg)](https://pypi.org/project/claude-tap/)
[![License](https://img.shields.io/github/license/liaohch3/claude-tap.svg)](https://github.com/liaohch3/claude-tap/blob/main/LICENSE)

[English](README.md)

拦截并查看 [Claude Code](https://docs.anthropic.com/en/docs/claude-code) 或 [Codex CLI](https://github.com/openai/codex) 的所有 API 流量。看清它们如何构造 system prompt、管理对话历史、选择工具、优化 token 用量——通过一个美观的 trace 查看器。

![演示](docs/demo_zh.gif)

![亮色模式](docs/viewer-zh.png)

<details>
<summary>暗色模式 / Diff 视图</summary>

![暗色模式](docs/viewer-dark.png)
![结构化 Diff](docs/diff-modal.png)
![字符级 Diff](docs/billing-header-diff.png)

</details>

## 安装

需要 Python 3.11+ 和 [Claude Code](https://docs.anthropic.com/en/docs/claude-code)（使用 `--tap-client codex` 时需要 [Codex CLI](https://github.com/openai/codex)）。

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

```bash
# OAuth 用户（ChatGPT Plus/Pro/Team）— 需指定 target
claude-tap --tap-client codex --tap-target https://chatgpt.com/backend-api/codex

# API Key 用户 — 默认 target 即可
claude-tap --tap-client codex

# 指定模型
claude-tap --tap-client codex -- --model codex-mini-latest

# 全自动模式（跳过所有权限确认）
claude-tap --tap-client codex -- --full-auto

# OAuth + 全自动 + 实时查看器
claude-tap --tap-client codex --tap-target https://chatgpt.com/backend-api/codex --tap-live -- --full-auto
```

### OpenClaw

[OpenClaw](https://github.com/openclaw-ai/openclaw)（npm: `openclaw`）是一个自托管 AI 智能体网关，通过内部的 [pi-ai](https://github.com/mariozechner/pi-ai) 管理 API 路由。不同模型服务商的配置方式有所不同。

**第一步 — 以纯代理模式启动 claude-tap**

根据要追踪的模型服务商选择对应的目标地址：

```bash
# Anthropic 系列模型（Claude）
claude-tap --tap-no-launch --tap-target https://api.anthropic.com --tap-port 19999 --tap-live

# OpenAI Codex 系列模型（ChatGPT OAuth）
claude-tap --tap-no-launch --tap-target https://chatgpt.com/backend-api --tap-port 19999 --tap-live
```

**第二步 — 将流量转发到代理**

使用 **Anthropic 系列模型**（Claude）时，通过 OpenClaw 的 `models.json` 覆盖 provider 配置即可，无需修改任何源文件：

```bash
AGENT_DIR=~/.openclaw/agents/main/agent
python3 -c "
import json, sys
path = '$AGENT_DIR/models.json'
try:
    cfg = json.load(open(path))
except Exception:
    cfg = {'providers': {}}
cfg['providers']['anthropic'] = {'baseUrl': 'http://127.0.0.1:19999'}
json.dump(cfg, open(path, 'w'), indent=2)
print('Done')
"
```

使用 **OpenAI Codex 系列模型**时，因为 Codex provider 使用了不受 `model.baseUrl` 控制的硬编码端点，需要对源文件进行一处修改：

```bash
PI_AI="$(npm root -g)/openclaw/node_modules/@mariozechner/pi-ai/dist"
sed -i '' \
  's|https://chatgpt.com/backend-api/codex/responses|http://127.0.0.1:19999/codex/responses|g' \
  "$PI_AI/providers/openai-codex-responses.js"
```

**第三步 — 禁用编译缓存后重启 OpenClaw**

Node.js 的模块编译缓存会导致修改后的文件被忽略：

```bash
NODE_DISABLE_COMPILE_CACHE=1 openclaw gateway --force
```

之后每次对话都会在实时查看器中生成新的 trace 记录。

> **Codex 用户注意：** 以上修改的是 `node_modules` 中的文件，执行 `npm update openclaw` 后会被覆盖，升级后需重新执行修改步骤。

### 浏览器预览

```bash
# 禁用退出后自动打开 HTML 查看器（默认开启）
claude-tap --tap-no-open

# 实时模式 — 客户端运行时在浏览器中实时查看
claude-tap --tap-live
claude-tap --tap-live --tap-live-port 3000    # 固定实时查看器端口
```

客户端退出后，也可以手动打开生成的查看器：

```bash
open .traces/trace_*.html
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
OPENAI_BASE_URL=http://127.0.0.1:8080/v1 codex

# Codex CLI（API Key）
claude-tap --tap-client codex --tap-no-launch --tap-port 8080
# 在另一个终端:
OPENAI_BASE_URL=http://127.0.0.1:8080/v1 codex
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
--tap-client CLIENT      启动的客户端: claude（默认）或 codex
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
--tap-proxy-mode MODE    代理模式: reverse（默认）或 forward
```

## 查看器功能

查看器是一个自包含的 HTML 文件（零外部依赖）：

- **结构化 Diff** — 对比相邻请求的变化：新增/删除的消息、system prompt diff、字符级高亮
- **路径过滤** — 按 API 端点筛选（如仅显示 `/v1/messages`）
- **模型分组** — 侧边栏按模型分组（Opus > Sonnet > Haiku）
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
2. 所有 API 请求流经: 代理 → 上游 API → 代理返回
3. SSE 流式响应实时转发（零额外延迟）
4. 每个请求-响应对记录到 `trace.jsonl`
5. 退出时生成自包含的 HTML 查看器
6. 实时模式（可选）通过 SSE 向浏览器广播更新

**核心特性:** 🔒 API key 自动脱敏 · ⚡ 零延迟 · 📦 自包含查看器 · 🔄 实时模式

## 许可证

MIT
