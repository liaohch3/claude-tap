# Claude Code 搭配 OrcaRouter

本指南介绍如何让 Claude Code 走 [OrcaRouter](https://www.orcarouter.ai) 的 Anthropic 兼容 API，同时用 `claude-tap` 捕获流量。

OrcaRouter 是兼容 OpenAI 与 Anthropic 的 AI 网关。它通过统一端点暴露跨多家模型的 provider/model 命名空间，并提供自适应路由、自动故障切换与零加成推理。用 `claude-tap` 捕获会话时，保持相同的 Claude Code 环境即可：`claude-tap` 会从 `ANTHROPIC_BASE_URL` 读取 OrcaRouter 上游，再把 Claude Code 指向本地代理。

English version: [Claude Code with OrcaRouter](orcarouter-claude-code.md).

## 环境变量

使用 `ANTHROPIC_AUTH_TOKEN` 并留空 `ANTHROPIC_API_KEY`，避免 Claude Code 的 API key 冲突提示。

```bash
export ANTHROPIC_AUTH_TOKEN="<你的 OrcaRouter API key>"
unset ANTHROPIC_API_KEY

export ANTHROPIC_MODEL="orcarouter/auto"
export ANTHROPIC_DEFAULT_OPUS_MODEL="orcarouter/auto"
export ANTHROPIC_DEFAULT_SONNET_MODEL="orcarouter/auto"
export ANTHROPIC_DEFAULT_HAIKU_MODEL="orcarouter/fusion-flash"
export CLAUDE_CODE_SUBAGENT_MODEL="orcarouter/fusion-flash"
export ANTHROPIC_BASE_URL=https://api.orcarouter.ai
```

OrcaRouter 的模型 id 带命名空间，例如 `orcarouter/auto`、`orcarouter/fusion`、`orcarouter/fusion-flash`、`orcarouter/fusion-mini`。用 `orcarouter/auto` 走跨模型自适应路由，或用具体 id 固定模型。

`claude-tap` 会用当前的 `ANTHROPIC_BASE_URL` 作为真实上游目标，然后再把启动的 Claude Code 进程指向本地代理。只有需要手动覆盖时才用 `--tap-target`。

## 用 claude-tap 捕获

正常运行 `claude-tap`：

```bash
claude-tap -- --permission-mode bypassPermissions
```

一次性非交互冒烟测试：

```bash
claude-tap \
  -- \
  --permission-mode bypassPermissions \
  -p 'Use Bash to run pwd, then reply with ORCAROUTER_CLAUDE_TAP_OK.'
```

进程退出后，打开生成的 viewer：

```bash
open .traces/*/trace_*.html
```

## 计价

claude-tap viewer 会依据捕获到的上游主机为每条请求计价。OrcaRouter 流量（`https://api.orcarouter.ai`）会被识别，并按底层厂商模型的费率计价——`orcarouter/auto` 之类的路由会解析到响应中实际报告的模型，因此按真实厂商费率计费，而非网关加成。

## 兼容性说明

OrcaRouter 的 Anthropic 兼容端点接受 Claude Code 默认发送的 `metadata.user_id` 值，因此无需请求归一化。`Authorization`、`x-api-key` 等常见认证头在录制前会被脱敏，但 prompt 与工具输出会原样保存——不要把密钥写进 prompt 或 agent 可能读取的文件内容。

## 验证记录

2026-08-29 验证：

- 直连 OrcaRouter Anthropic API（`POST /v1/messages`）返回 HTTP `200`
- 直连 OrcaRouter OpenAI 兼容接口（`POST /v1/chat/completions`）返回 HTTP `200`
- `ANTHROPIC_BASE_URL=https://api.orcarouter.ai`
- 主 Claude Code 轮使用 `orcarouter/auto`
- `api.orcarouter.ai` 主机对应计价命名空间已由单元测试覆盖

## 参考

- [OrcaRouter](https://www.orcarouter.ai)
