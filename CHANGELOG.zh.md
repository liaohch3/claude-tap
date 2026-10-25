# 更新日志

本文件记录与英文 [CHANGELOG.md](CHANGELOG.md) 对应的新版本变更；更早的历史记录见英文日志。

## [Unreleased]

### 变更

- 在对话历史、轮次差异和响应中显示 Responses 推理文字，包括服务商写在 `content`、`summary` 为空的推理。
- 由文本块组成的工具输出（例如 Codex `exec` 的结果）按纯文本显示，不再显示为转义后的 JSON 字符串。
- 自定义工具调用的输入（例如 Codex 发给 `exec` 的 JavaScript）按纯文本显示。
- 显示 `namespace` 工具的成员，以及 `format`、`external_web_access` 等其他工具定义字段。

## [0.1.146] - 2026-10-17

### 变更

- 使用 Python 定价适配器计算查看器中的费用。
- 在查看器的轮次详情中解释提示缓存未命中的原因。
- 展示会话标题时区分工具框架注入的内容和用户提示。
- 标记占据会话轮次大量内容的工具结果。

### 修复

- 修复 `urlsplit()` 抛出 `ValueError` 导致 Dashboard 记录接口和 HTML 查看器返回 HTTP 500 的问题；隐藏受影响的展示文本和预览，保留存储的原始记录。
- 参数差异比较忽略对象键顺序，并对展示的 JSON 排序以突出实际值变化。
- 将 claude-tap 的运行提示输出到 stderr，确保各模式均保留被包装客户端的 stdout。
- dsh 使用 Anthropic 供应商时抓取 Anthropic 协议请求（`/v1/messages`）。
- 避免 Dashboard 会话 token 总数重复计算缓存输入。

