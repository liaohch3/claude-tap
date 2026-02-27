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

```bash
# 基本用法 — 启动带 trace 的 Claude Code
claude-tap

# 实时模式 — 在浏览器中实时观察 API 调用
claude-tap --tap-live

# 透传参数给 Claude Code
claude-tap -- --model claude-opus-4-6
claude-tap -c    # 继续上次对话

# 追踪 Codex CLI 而非 Claude Code
claude-tap --tap-client codex
claude-tap --tap-client codex -- --model codex-mini-latest
```

客户端退出后，打开生成的 HTML 查看器：

```bash
open .traces/trace_*.html
```

### CLI 选项

除以下 `--tap-*` 参数外，所有参数均透传给所选客户端：

```
--tap-client CLIENT      启动的客户端: claude（默认）或 codex
--tap-live               启动实时查看器（自动打开浏览器）
--tap-live-port PORT     实时查看器端口（默认: 自动分配）
--tap-open               退出后自动在浏览器中打开 HTML 查看器
--tap-output-dir DIR     Trace 输出目录（默认: ./.traces）
--tap-port PORT          代理端口（默认: 自动分配）
--tap-target URL         上游 API 地址（默认: 根据客户端自动选择）
--tap-no-launch          仅启动代理，不启动客户端
--tap-max-traces N       最大保留 trace 数量（默认: 50，0 = 不限）
--tap-no-update-check    禁用启动时的 PyPI 更新检查
--tap-no-auto-update     仅检查更新，不自动下载
```

**纯代理模式**（适用于自定义场景）：

```bash
claude-tap --tap-no-launch --tap-port 8080
# 在另一个终端:
ANTHROPIC_BASE_URL=http://127.0.0.1:8080 claude
```

### Codex CLI 支持

追踪 [Codex CLI](https://github.com/openai/codex)（OpenAI）而非 Claude Code：

```bash
# 启动带 trace 的 Codex
claude-tap --tap-client codex

# 指定模型
claude-tap --tap-client codex -- --model codex-mini-latest
```

在反向代理模式（默认）下，claude-tap 通过设置 `OPENAI_BASE_URL` 将 Codex 流量路由到代理。上游目标默认为 `https://api.openai.com`。

**前提条件:** 已安装 Codex CLI，且环境变量中已设置 `OPENAI_API_KEY`。

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
