# 本地 AI Agent Trace Viewer

`claude-tap` 是面向 AI 编程 Agent 的本地 trace viewer 和 HTML 导出工具。它可以查看 Claude Code traces、Codex traces、OpenAI Responses API traces、Anthropic Messages traces、Gemini traces 和其他 agent runs，不需要把私有数据上传到云端 dashboard。

[English guide](agent-trace-viewer.md) | 中文指南

## 什么是 AI agent trace？

AI agent trace 是一次 agent 运行背后的请求和响应链路记录。对于编程 Agent，一个有用的 trace 通常包括：

- System prompt 和对话历史
- 工具 schema、工具调用、工具输入和工具结果
- 重建后的流式响应内容
- Token 用量、cache 用量和延迟
- 相邻轮次之间的请求 diff

这些内容很难只从终端输出里恢复。终端显示 agent 说了什么；trace 显示 agent 实际发出了什么。

## 为什么要本地查看 trace？

很多 observability 产品适合生产系统，但本地调试的目标不同。当编程 Agent 接触私有代码、私有 prompt、仓库元数据或内部工具时，最稳妥的默认方式是在自己的机器上检查 trace。

`claude-tap` 默认把 trace session 留在本地。常见认证 header 会在记录前脱敏，导出的 HTML 文件也是由你自己控制的静态 artifact。

## 支持的 traces 和客户端

`claude-tap` 可以追踪和查看这些客户端的会话：

- Claude Code
- Codex CLI
- Gemini CLI
- Cursor CLI
- OpenCode
- Kimi CLI
- Pi
- Hermes Agent
- Qoder CLI
- Antigravity CLI
- CodeBuddy CLI

它也支持 Anthropic Messages、OpenAI Responses、OpenAI Chat Completions、Gemini 和 Claude 兼容网关等 trace 形态。

## 如何查看 trace

安装 `claude-tap`：

```bash
uv tool install claude-tap
```

通过 `claude-tap` 启动客户端：

```bash
# Claude Code
claude-tap

# Codex CLI
claude-tap --tap-client codex

# Gemini CLI
claude-tap --tap-client gemini -- -p "hello"
```

打开本地 dashboard，或导出独立 HTML 文件：

```bash
claude-tap export --format html trace.jsonl
```

## 先看哪些内容？

可以从这些问题开始：

- Agent 是否收到了你预期的 prompt 和上下文？
- 多轮之间工具 schema 是否发生了变化？
- Agent 是否用正确参数调用了正确工具？
- Token 增长来自历史、工具结果，还是重复上下文？
- 延迟来自模型调用、工具调用，还是很长的流式响应？

trace viewer 的设计就是围绕这些调试问题展开的。

## Claude trace viewer

对于 Claude Code 和 Anthropic 兼容流量，`claude-tap` 可以展示 Anthropic Messages 请求、工具调用、流式响应、token 用量和 Claude 兼容网关元数据。当你想在不上传 session 数据的情况下查看 Claude Code runs，它可以作为本地 Claude trace viewer 使用。

## Codex trace viewer

对于 Codex CLI，`claude-tap` 支持 OpenAI API key 模式和 ChatGPT 订阅 OAuth 模式。它可以查看 OpenAI Responses API 流量、WebSocket 记录、工具调用、reasoning/output 区块、token 用量和请求 diff。

## 导出 trace 到 HTML

HTML 导出适合生成可移植的 review artifact：

- 和其他 maintainer 分享一次调试运行
- 给 pull request 附上证据
- 归档一次模型行为回归
- 在 prompt 或工具变更期间对比相邻请求

导出的文件是自包含的，不依赖托管 dashboard。

## 本地 trace viewer 和托管 observability 的区别

当你需要快速检查私有 agent runs 时，适合使用本地 trace viewer。当你需要生产监控、团队 dashboard、告警或长期遥测时，适合使用托管 observability。

`claude-tap` 关注的是本地调试和 review 流程：捕获一次真实运行，检查请求链路，并在需要时导出静态 artifact。

## 下一步

- [打开 GitHub 仓库](https://github.com/liaohch3/claude-tap)
- [查看支持的客户端](../support-matrix.zh.md)
- [Read the English guide](agent-trace-viewer.md)
