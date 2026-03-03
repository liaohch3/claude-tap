---
owner: claude-tap-maintainers
last_reviewed: 2026-03-03
source_of_truth: AGENTS.md
---

# E2E 验证要求

如果变更影响 proxying、trace 捕获、CLI session 流程、auth 处理或其他端到端行为，在开 PR 前必须运行真实 E2E 验证。

优先命令：

```bash
uv run pytest tests/e2e/ --run-real-e2e --timeout=300
uv run pytest tests/e2e/test_real_proxy.py::TestRealProxy::test_single_turn --run-real-e2e --timeout=180
```

手动替代方案：

```bash
scripts/run_real_e2e.sh
scripts/run_real_e2e_tmux.sh
```

如果无法运行真实 E2E（例如缺少 auth/token），在 PR 正文中记录原因与剩余风险。

# E2E 对话规则

每次 E2E 运行必须至少包含一次完整的多轮对话。
对于对话验证和截图证据，请使用 tmux 交互流程（`scripts/run_real_e2e_tmux.sh`）。
不要使用 `claude -p` 的单次运行作为对话完整性的证明。

# UI 证据要求

对于会改变 UI 布局、样式、交互流程或渲染内容的 PR：

- 每个变更的页面/状态至少提供一张截图。
- 当视觉差异重要时，提供变更前后截图。
- 当移动端行为受影响时，提供移动端截图。
- 使用来自 `.traces/trace_*.jsonl` 或真实运行输出的真实 trace 产物。
- 对于与 E2E 相关的 UI 变更，截图必须来自至少完成一次完整多轮对话的运行。

# 截图质量门禁

作为 PR 证据提交的每张截图，在 `git add` 之前都必须通过以下检查：

## 强制检查
1. **Viewport 宽度 ≥ 1280px** — Headless browser 常默认窄 viewport。截图前务必调整为桌面尺寸（1280x800 或 1440x900）。
2. **内容与声明一致** — 如果 PR 写的是“已捕获 WS trace”，截图中必须清楚显示 WS trace，而不是其他请求或加载页。
3. **无编码损坏** — Unicode 箭头（→←）、CJK 字符和 emoji 必须正确渲染。如有疑问，在生成证据页面时使用 ASCII 等价字符或 HTML entity。
4. **无错误页面** — 404、ERR_EMPTY_RESPONSE、空白页或 “page not found” 不能作为证据。
5. **最小分辨率** — 图像宽度必须 ≥ 1000px。更窄通常是移动端/平板截图。
6. **文件大小合理** — 小于 10KB 的截图通常是空白页或错误页。典型 trace viewer 截图为 100KB–500KB。

## 最佳实践
- 对日志/文本证据，优先渲染为有样式的 HTML（深色卡片、monospace、语法高亮），不要直接提供原始 `.log` 文件，以避免字体/编码问题。
- 截取 trace viewer 时，先导航到指定 entry，再截图。
- 使用 `scripts/check_screenshots.sh` 自动执行 pre-commit 验证。

## 反模式：盲目提交
不要在未先打开并检查截图的情况下直接 `git add` + `git commit` + `git push`。这会浪费 reviewer 时间并削弱证据信任。
