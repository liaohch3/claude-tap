# Mock E2E 测试模式：Fake Upstream + Fake Claude

**日期：** 2026-02-25
**标签：** testing, E2E, mock, best-practice

## 模式描述

现有 E2E 测试套件（`tests/test_e2e.py`）采用了全 mock 方案，
可在无外部依赖的情况下测试整个 claude-tap pipeline：

1. **Fake upstream server** - 在后台线程运行 aiohttp server，
   模拟 Anthropic API，同时返回 non-streaming 与 streaming（SSE）响应。

2. **Fake Claude script** - 在 PATH 中放置临时 Python 脚本作为 `claude` CLI。
   它向 `ANTHROPIC_BASE_URL`（由 claude-tap 设置为 proxy 地址）发送 HTTP 请求并打印结果。

3. **真实 claude-tap** - 以子进程方式运行真实 `claude_tap` module，
   并通过 `--tap-target` 指向 fake upstream。

## 为什么效果好

- **无外部依赖**：测试可离线运行，无需 API keys
- **可确定性**：相同输入始终产生相同输出
- **速度快**：无网络时延，无 rate limit
- **覆盖完整**：测试全链路，包括 proxy 启动、请求转发、SSE 重组、JSONL 记录、HTML viewer 生成、API key 脱敏
- **边界情况稳健**：覆盖上游错误（500）、畸形 SSE、大 payload（100KB+）

## 关键实现细节

- `run_fake_upstream_in_thread()` 使用 `threading.Event` 做同步
- Fake Claude 脚本由 `_create_fake_claude()` 创建并设为可执行
- 临时 bin 目录会 prepend 到 `PATH`，让 claude-tap 找到 fake `claude`
- 端口硬编码（19199、19200 等）保持测试隔离
- Trace 文件写入 `tempfile.mkdtemp()`，断言后清理

## 何时使用此模式

适用于：
- 测试 proxy 行为（转发、记录、SSE 处理）
- 测试 HTML viewer 生成
- 测试 header 脱敏与安全能力
- 在无 Claude API 访问的 CI 中运行

## 互补模式

若要测试真实 Claude 集成（实际 API 响应、tool use、多轮对话），
请参考 `tests/e2e/` 下的 real E2E 测试。它们需要可用的 `claude` CLI 安装，
且在 CI 中默认跳过。
