# claude-tap

[![PyPI version](https://img.shields.io/pypi/v/claude-tap.svg)](https://pypi.org/project/claude-tap/)
[![PyPI downloads](https://img.shields.io/pypi/dm/claude-tap.svg)](https://pypi.org/project/claude-tap/)
[![Python version](https://img.shields.io/pypi/pyversions/claude-tap.svg)](https://pypi.org/project/claude-tap/)
[![License](https://img.shields.io/github/license/liaohch3/claude-tap.svg)](https://github.com/liaohch3/claude-tap/blob/main/LICENSE)

[English](README.md)

一个 CLI 工具，通过本地反向代理包裹 [Claude Code](https://docs.anthropic.com/en/docs/claude-code)，拦截并记录所有 API 请求。适用于研究 Claude Code 的 **Context Engineering**——它如何构造 system prompt、管理对话历史、选择工具，以及在多轮对话中优化 token 用量。

## 工作原理

```
claude-tap
  ├─ 启动本地 HTTP 反向代理 (127.0.0.1:PORT)
  ├─ 以 ANTHROPIC_BASE_URL=http://127.0.0.1:PORT 启动 Claude Code
  ├─ Claude Code 将请求发送到代理（纯 HTTP，无需 TLS）
  ├─ 代理通过 HTTPS 将请求转发到 api.anthropic.com
  ├─ SSE 流式响应实时转发（零额外延迟）
  ├─ 每个请求-响应对记录到 JSONL trace 文件
  └─ 退出时生成自包含的 HTML 查看器
```

## 快速开始

### 前置条件

- Python 3.11+
- [uv](https://github.com/astral-sh/uv)（推荐）或 pip
- 已安装 [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI

### 安装

```bash
# 使用 uv 全局安装（推荐）
uv tool install claude-tap

# 或使用 pip
pip install claude-tap
```

安装后，`claude-tap` 命令即可全局使用。

### 升级

```bash
uv tool upgrade claude-tap
# 或: pip install --upgrade claude-tap
```

### 运行

```bash
# 启动带 trace 的 Claude Code
claude-tap

# 传递参数给 Claude Code
claude-tap -- --model claude-opus-4-6
```

### 查看 Trace

Claude Code 退出后，工具会在 `./.traces/` 目录下生成三个文件：

| 文件 | 说明 |
|------|------|
| `trace_YYYYMMDD_HHMMSS.jsonl` | 原始 trace 数据（每个 API 调用一条 JSON 记录） |
| `trace_YYYYMMDD_HHMMSS.log` | 代理调试日志 |
| `trace_YYYYMMDD_HHMMSS.html` | 自包含 HTML 查看器（在任意浏览器中打开） |

```bash
open .traces/trace_*.html
```

## CLI 选项

```
用法: claude-tap [-h] [-v] [--tap-*] [CLAUDE_ARGS...]

以下 tap 专用参数不会转发给 claude，其余参数均直接透传:
  -h, --help             显示帮助信息并退出
  -v, --version          显示版本号并退出
  --tap-output-dir DIR   Trace 输出目录（默认: ./.traces）
  --tap-port PORT        代理端口（默认: 0 = 自动分配）
  --tap-target URL       上游 API 地址（默认: https://api.anthropic.com）
  --tap-no-launch        仅启动代理，不启动 Claude Code
  --tap-open             退出后自动在浏览器中打开 HTML 查看器
  --tap-live             启动实时查看器（自动打开浏览器）
  --tap-live-port PORT   实时查看器端口（默认: 自动分配）
```

例如，`claude-tap -c` 会继续上次对话（`-c` 会被转发给 claude）。

**实时模式** —— 实时观察 API 调用：

```bash
claude-tap --tap-live
```

自动打开浏览器，实时显示 Claude 发出的每个请求。

**纯代理模式**（适用于自定义场景）：

```bash
claude-tap --tap-no-launch --tap-port 8080
# 在另一个终端:
ANTHROPIC_BASE_URL=http://127.0.0.1:8080 claude
```

## 截图

### 亮色模式
![亮色模式](docs/viewer-light.png)

### 暗色模式
![暗色模式](docs/viewer-dark.png)

### 结构化 Diff
对比相邻 API 请求，查看具体变化——新增/删除的消息、system prompt diff、参数变化。字符级高亮精确定位差异：

![结构化 Diff](docs/diff-modal.png)
![字符级 Diff](docs/billing-header-diff.png)

### 中文界面
![中文界面](docs/viewer-zh.png)

## HTML 查看器功能

查看器是一个自包含的 HTML 文件（零外部依赖），提供以下功能：

- **路径过滤** —— 按 API 端点筛选（如仅显示 `/v1/messages`）
- **模型分组** —— 侧边栏按模型分组展示请求（Opus > Sonnet > Haiku）
- **可展开区块** —— System Prompt、消息、工具、响应、Token 用量、SSE 事件、完整 JSON
- **工具检查器** —— 可展开的卡片，显示工具名称、描述和参数 schema
- **Token 用量分析** —— 输入 / 输出 / 缓存读取 / 缓存创建
- **结构化 Diff** —— 对比相邻同模型请求的变化：
  - 高亮新增/删除/修改的消息
  - System prompt 逐行文本 diff
  - 行内变化的字符级高亮（如 `cch=bea5f` → `cch=5406b`）
  - 工具和参数级字段 diff
- **搜索与过滤** —— 全文搜索消息、工具、prompt 和响应
- **暗色模式** —— 切换亮色/暗色主题（跟随系统偏好）
- **键盘导航** —— `j`/`k` 或方向键在轮次间切换
- **复制助手** —— 一键复制请求 JSON 或 cURL 命令
- **多语言** —— 支持 8 种语言：英语、简体中文、日语、韩语、法语、阿拉伯语（RTL）、德语、俄语

## JSONL 记录格式

`.jsonl` 文件中每行是一个 JSON 对象：

```json
{
  "timestamp": "2026-02-15T10:30:00.000Z",
  "request_id": "req_a1b2c3d4e5f6",
  "turn": 1,
  "duration_ms": 1234,
  "request": {
    "method": "POST",
    "path": "/v1/messages",
    "headers": { "x-api-key": "sk-ant-api03-..." },
    "body": { "model": "...", "system": "...", "messages": ["..."], "tools": ["..."] }
  },
  "response": {
    "status": 200,
    "headers": { "..." : "..." },
    "body": { "id": "msg_...", "content": ["..."], "usage": { "..." : "..." } },
    "sse_events": [ { "event": "message_start", "data": { "..." : "..." } } ]
  }
}
```

> API key 在 trace 中会自动脱敏（仅保留前 12 个字符 + `...`）。

## 项目结构

```
claude-tap/
├── claude_tap/
│   ├── __init__.py       # 核心 CLI：反向代理 + Claude 启动器
│   ├── __main__.py       # python -m claude_tap 入口
│   └── viewer.html       # 自包含 HTML 查看器模板
├── tests/
│   └── test_e2e.py       # 端到端 + 单元测试（10 个测试用例）
├── .github/workflows/
│   ├── ci.yml            # Push/PR 时执行 lint + 测试
│   └── publish.yml       # Tag 时发布到 PyPI
├── pyproject.toml        # 项目元数据和依赖
├── LICENSE               # MIT
└── .traces/              # 输出目录（自动创建）
```

### 核心组件

| 组件 | 说明 |
|------|------|
| `SSEReassembler` | 解析原始 SSE 字节流，使用 Anthropic SDK 的 `accumulate_event()` 重建完整 Message 对象 |
| `TraceWriter` | 基于 `asyncio.Lock` 的异步安全 JSONL 写入器，追踪 token 统计 |
| `LiveViewerServer` | 基于 SSE 的实时 trace 查看 HTTP 服务器 |
| `proxy_handler` | aiohttp 通配路由，转发请求并记录响应 |
| `run_claude` | 启动 Claude Code 子进程，设置正确的环境变量，转发 SIGINT |

### 设计决策

- **HTTP 而非 HTTPS**：本地代理使用纯 HTTP（`127.0.0.1`），避免 TLS 证书的复杂性。Claude Code 通过 `ANTHROPIC_BASE_URL` 连接。
- **零延迟流式传输**：SSE 数据块通过 `resp.write(chunk)` 立即转发，同时并行缓冲用于记录。
- **基于 SDK 的重组**：使用 `anthropic.lib.streaming._messages.accumulate_event()` 精确重建 Message，而非自定义解析。
- **自包含 HTML**：查看器模板零外部依赖，trace 数据直接嵌入 HTML 文件中。

## 测试

```bash
# 运行所有测试
uv run pytest

# 详细输出
uv run pytest -v
```

测试覆盖：基础流式/非流式请求、上游错误（5xx）、畸形 SSE、大负载（100KB+）、并发请求、参数解析、请求头过滤、SSE 重组边界用例、上游不可达、实时查看器服务。

## 许可证

MIT
