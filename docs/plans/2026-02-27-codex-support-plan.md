---
status: completed
---

# Codex 支持计划

日期：2026-02-27

## 目标

为 `claude-tap` 增加一等公民级的 Codex 支持，同时保持 Claude 行为完全向后兼容。

## 范围

- 增加 client 选择：`--tap-client {claude,codex}`。
- 默认 client 保持为 `claude`。
- 对 reverse proxy mode 的 `codex` 注入 `OPENAI_BASE_URL=http://127.0.0.1:<port>/v1`。
- 保持现有 Claude 行为：`ANTHROPIC_BASE_URL=http://127.0.0.1:<port>`。
- 当省略 `--tap-target` 时，按 client 设置默认 upstream target：
  - `claude` -> `https://api.anthropic.com`
  - `codex` -> `https://api.openai.com`
- 保留现有 forward proxy 行为。
- 增加 trace metadata `upstream_base_url`，提升 viewer cURL 重建能力。

## 非目标

- 在本次变更中重命名 package/project 品牌（`claude-tap`）。
- 在 Claude/Codex 之外增加广泛 provider 抽象。
- 保证 Codex ChatGPT 登录流在 forward proxy mode 下可用。

## 测试策略

- Unit / mock E2E 回归：
  - `uv run pytest tests/test_e2e.py -x --timeout=120`
- 新增 Codex mock E2E：
  - fake `codex` binary + fake upstream API
  - 断言 reverse-mode 请求路径与 `upstream_base_url` trace 字段
- 仓库 gate 检查：
  - `uv run ruff check .`
  - `uv run ruff format --check .`
  - `uv run pytest tests/ -x --timeout=60`

## 成功标准

- Claude 默认工作流不变。
- `--tap-client codex` 可启动 `codex` 并写出有效 trace 输出。
- viewer copy-cURL 在存在时使用 `upstream_base_url`。
- 现有测试套件无回归。

## 真实 E2E 验收规则

对 Codex 真实 E2E 验证，HTTP `403/401` 视为硬失败。
仅当端到端请求以上游成功响应完成时，才视为运行成功。

## 风险

- 即便 proxy 连接正确，Codex 账号 scopes 也可能阻塞 model-list 或 response API（`403`）。
- Codex ChatGPT-web 路由流量可能无法被当前 forward mode 假设完整覆盖。
