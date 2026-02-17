# claude-tap

[![PyPI version](https://img.shields.io/pypi/v/claude-tap.svg)](https://pypi.org/project/claude-tap/)
[![PyPI downloads](https://img.shields.io/pypi/dm/claude-tap.svg)](https://pypi.org/project/claude-tap/)
[![Python version](https://img.shields.io/pypi/pyversions/claude-tap.svg)](https://pypi.org/project/claude-tap/)
[![License](https://img.shields.io/github/license/liaohch3/claude-tap.svg)](https://github.com/liaohch3/claude-tap/blob/main/LICENSE)

[English](README.md)

拦截并查看 [Claude Code](https://docs.anthropic.com/en/docs/claude-code) 的所有 API 流量。看清它如何构造 system prompt、管理对话历史、选择工具、优化 token 用量——通过一个美观的 trace 查看器。

![亮色模式](docs/viewer-zh.png)

<details>
<summary>暗色模式 / Diff 视图</summary>

![暗色模式](docs/viewer-dark.png)
![结构化 Diff](docs/diff-modal.png)
![字符级 Diff](docs/billing-header-diff.png)

</details>

## 安装

需要 Python 3.11+ 和 [Claude Code](https://docs.anthropic.com/en/docs/claude-code)。

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
```

Claude Code 退出后，打开生成的 HTML 查看器：

```bash
open .traces/trace_*.html
```

### CLI 选项

除以下 `--tap-*` 参数外，所有参数均透传给 Claude Code：

```
--tap-live             启动实时查看器（自动打开浏览器）
--tap-live-port PORT   实时查看器端口（默认: 自动分配）
--tap-open             退出后自动在浏览器中打开 HTML 查看器
--tap-output-dir DIR   Trace 输出目录（默认: ./.traces）
--tap-port PORT        代理端口（默认: 自动分配）
--tap-target URL       上游 API 地址（默认: https://api.anthropic.com）
--tap-no-launch        仅启动代理，不启动 Claude Code
```

**纯代理模式**（适用于自定义场景）：

```bash
claude-tap --tap-no-launch --tap-port 8080
# 在另一个终端:
ANTHROPIC_BASE_URL=http://127.0.0.1:8080 claude
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

1. `claude-tap` 启动反向代理，并以 `ANTHROPIC_BASE_URL` 指向代理来启动 Claude Code
2. 所有 API 请求流经: 代理 → Anthropic API → 代理返回
3. SSE 流式响应实时转发（零额外延迟）
4. 每个请求-响应对记录到 `trace.jsonl`
5. 退出时生成自包含的 HTML 查看器
6. 实时模式（可选）通过 SSE 向浏览器广播更新

**核心特性:** 🔒 API key 自动脱敏 · ⚡ 零延迟 · 📦 自包含查看器 · 🔄 实时模式

## 许可证

MIT
