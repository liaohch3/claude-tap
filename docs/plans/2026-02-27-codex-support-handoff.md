---
status: completed
---

# Codex 支持交接（详细）

日期：2026-02-27
分支：`feat/codex-client-support`
仓库：`liaohch3/claude-tap`

## 1. 目标、范围与当前状态

### 原始目标

在保留现有 Claude 行为的同时，新增对 Codex 的支持。

### 来自用户的验收约束

真实 Codex 验证必须端到端成功。任何 `403` 都视为硬失败。

### 当前状态

- `--tap-client codex` 的核心实现已完成。
- mock/unit/integration 测试在本地通过。
- 真实 Codex 运行仍受账号/API 权限（`Missing scopes: api.model.read`）和当前环境中的上游行为阻塞。
- 工作已提交到分支 `feat/codex-client-support`。

## 2. 已完成内容（以及原因）

### A. CLI 与运行时行为

#### 文件：`claude_tap/cli.py`

已做变更：

- 增加 client 选择支持：
  - 新增参数 `--tap-client`，取值 `claude|codex`，默认 `claude`。
- 扩展启动路径，使其可运行 `claude` 或 `codex`。
- 反向 proxy 环境注入改为按 client 区分：
  - Claude：`ANTHROPIC_BASE_URL=http://127.0.0.1:<port>`
  - Codex：`OPENAI_BASE_URL=http://127.0.0.1:<port>/v1`
- 省略 `--tap-target` 时，默认值按 client 推导：
  - Claude -> `https://api.anthropic.com`
  - Codex -> `https://api.openai.com`
- 更新面向用户的启动/停止消息，使其反映所选 client。
- 保留现有 Claude forward-mode 行为，包括仅对 Claude 的 `--settings` 注入路径。

原因：

- 现有实现仅支持 Claude，且围绕 Anthropic 变量硬编码。
- Codex 在 reverse mode 下需要 OpenAI 风格的 base URL 行为。
- 默认保持 `claude` 可确保向后兼容。

### B. 面向 Viewer 的 Trace 模型增强

#### 文件：`claude_tap/proxy.py`

已做变更：

- 通过 `_build_record(...)` 在每条 trace record 中新增 `upstream_base_url`。
- 在 streaming 与 non-streaming handler 中贯穿传递 `upstream_base_url`。
- 通过强制 `Accept-Encoding: identity` 简化上游编码行为，避免当前环境中的 zstd 兼容问题。

原因：

- viewer copy-curl 原先硬编码 Anthropic 域名；需要按来源重建上游地址。
- Codex 与部分响应在该环境中出现 zstd 解码失败；使用 identity 编码可降低 proxy 侧兼容风险。

### C. Forward Proxy 一致性

#### 文件：`claude_tap/forward_proxy.py`

已做变更：

- 在转发上游请求时同样强制 `Accept-Encoding: identity`。

原因：

- 与 reverse proxy 路径保持一致，减少压缩相关失败。

### D. Viewer 行为

#### 文件：`claude_tap/viewer.html`

已做变更：

- `copyCurl(...)` 在可用时使用 `entry.upstream_base_url`。
- 对旧 trace 回退到 `https://api.anthropic.com`。

原因：

- 使生成的 curl 命令对 Codex/OpenAI trace 也准确。
- 保持对现有旧 trace 文件的向后兼容。

### E. 测试覆盖更新

#### 文件：`tests/test_e2e.py`

已做变更：

- 为 `_run_claude_tap(...)` helper 增加 `tap_client` 参数。
- `test_parse_args` 现在验证 Codex 默认值：
  - `--tap-client codex`
  - 默认 target -> `https://api.openai.com`
- 新增 `test_codex_client_reverse_proxy`，使用 fake `codex` 可执行文件：
  - fake codex 使用 `OPENAI_BASE_URL`
  - fake upstream 期望路径 `/v1/messages`
  - 断言 trace 包含预期 path/model
  - 断言已记录 `upstream_base_url`
  - 断言启动输出包含 `OPENAI_BASE_URL=...`

原因：

- 在不依赖真实外部凭据的前提下验证新 client 行为。
- 确保参数解析与运行时连接路径不回归。

### F. 计划文档

#### 文件：`docs/plans/2026-02-27-codex-support-plan.md`

已做变更：

- 新增聚焦范围的实现计划与明确的验收/风险说明。

原因：

- 遵循仓库关于计划文档与清晰交接上下文的规范。

## 3. 尚未完成（或未完全完成）内容

### A. 真实 Codex E2E 成功

由于环境/账号限制，尚未达成。

观察到的阻塞：

- Codex 模型刷新请求失败：
  - `GET /v1/models?client_version=...`
  - `403 Forbidden`
  - 消息包含 `Missing scopes: api.model.read`

影响：

- 按用户要求，这意味着真实验证尚未完成。

### B. 专用 `tests/e2e/` 真实 Codex 测试套件

尚未添加。

原因：

- 由于硬性要求是真实运行必须成功，在权限未修复前新增真实测试将稳定失败。

### C. README / README_zh 面向用户文档

本轮未更新。

原因：

- 优先级先放在代码路径与正确性。
- 后续应补充 `--tap-client` 新行为与示例。

## 4. 精确测试执行与结果

以下命令已在本地运行并通过：

- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run pytest tests/ -x --timeout=60`
  - 结果：`48 passed, 18 skipped`

额外执行的定向测试：

- `uv run pytest tests/test_e2e.py -k "test_parse_args or test_codex_client_reverse_proxy" -x --timeout=120`
  - 通过
- `uv run pytest tests/test_e2e.py -k "test_e2e or test_forward_proxy_connect or test_codex_client_reverse_proxy" -x --timeout=120`
  - 通过

尝试真实 Codex smoke 运行并失败（该环境预期如此）：

- 命令模式：
  - `uv run python -m claude_tap --tap-client codex --tap-target https://api.openai.com --tap-no-update-check --tap-no-open -- exec "Reply with exactly: CODEX_REAL_OK" --skip-git-repo-check --json`
- 失败指标：
  - `403 Forbidden`，缺少 `api.model.read`
  - Codex 非零退出

## 5. 工作树卫生 / Commit 范围

重要上下文：

- 本次工作前已存在 `uv.lock` 修改。
- `log/` 含运行产物，处于未跟踪状态。
- 以上已被有意排除在 commit 范围外。

本任务 commit 计划包含的文件：

- `claude_tap/cli.py`
- `claude_tap/proxy.py`
- `claude_tap/forward_proxy.py`
- `claude_tap/viewer.html`
- `tests/test_e2e.py`
- `docs/plans/2026-02-27-codex-support-plan.md`
- `docs/plans/2026-02-27-codex-support-handoff.md`（本文件）

## 6. 给下一轮 Codex 流程的建议动作

### 1) 先解决真实凭据/Scope 阻塞

- 确保 API key/project/org 拥有 `api.model.read`（以及所需响应 scope）。
- 重新运行真实 Codex smoke 命令，确认不再返回 403。

### 2) 添加真实 Codex E2E 覆盖

建议新增：

- 在 `tests/e2e/` 下新增 Codex 真实运行模块。
- 至少覆盖：
  - 单轮成功
  - 多轮连续性
  - trace 生成与路径断言
- 按用户规则，`403/401` 保持硬失败。

### 3) 更新用户文档

- 更新 `README.md`，必要时更新 `README_zh.md`，补充：
  - `--tap-client codex`
  - Codex reverse mode 示例
  - 已知前置条件：正确的 OpenAI scopes

### 4) 可选：深入排查 zstd 错误路径

- 即使 proxy 到 upstream 已偏好 identity，Codex 在失败场景仍可能记录 zstd 解码问题。
- 需要确认是否与非代理流量、fallback 通道或 Codex 特定内部 endpoint 有关。

## 7. 下一位 Agent Prompt 的快速技术摘要

如果你需要给另一个 Codex 流程一个简短启动提示：

- 分支：`feat/codex-client-support`
- 核心功能已完成：`--tap-client codex` + OpenAI reverse mode 环境注入。
- `tests/` 本地通过（`48 passed, 18 skipped`）。
- 真实 Codex 仍被 `403 missing scopes: api.model.read` 阻塞。
- 后续先修凭据/scopes，再补真实 Codex E2E 测试与 README 更新。
