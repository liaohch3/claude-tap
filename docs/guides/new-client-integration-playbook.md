# Playbook：为 claude-tap 添加新的 LLM Client

提炼自 Codex 集成（PR #12，2026-02-28）。将其作为可复用框架，用于为任意新的 LLM client（如 Gemini CLI、Grok CLI 等）添加支持。

---

## 第 1 阶段：侦察 - 理解 Client 的 Wire Protocol

在写代码前，先回答以下问题：

1. **Client 调用的是哪个 API endpoint？**（例如 `api.openai.com/v1/responses`、
   `api.anthropic.com/v1/messages`）
2. **它是否有备用 endpoint？**（例如 Codex 对 ChatGPT Plus 用户使用 `chatgpt.com/backend-api/codex`，而不是 `api.openai.com`）
3. **它使用什么传输方式？** HTTP POST？WebSocket？gRPC？
   - **Codex 经验**：Codex v0.106.0 在无提示情况下把 `/v1/responses` 从 HTTP 切到 WebSocket。HTTP proxy 看不到任何内容。务必验证实际 wire transport，而不是只看文档。
4. **哪个 env var 控制 base URL？**（`OPENAI_BASE_URL`、`ANTHROPIC_BASE_URL` 等）
5. **子进程是否真的继承了该 env var？**（Codex 有 Rust 子进程，可能会或可能不会遵循 Node.js 父进程环境）
6. **它使用何种编码/压缩？**（Codex 会发送 zstd 压缩体）

### 如何调查

```bash
# Watch actual network traffic
lsof -i -P | grep <process_name>

# Check what the process sees
ps -p <pid> -E | tr ' ' '\n' | grep BASE_URL

# Intercept with mitmproxy for full visibility
mitmproxy --mode reverse:https://api.example.com --listen-port 8080
```

**关键原则**：不要盲信文档。观察真实行为。

---

## 第 2 阶段：Proxy Wiring - 让每个请求都可见

### Checklist

- [ ] 设置正确的 env var，将流量重定向到 claude-tap 本地 proxy
- [ ] 处理路径映射（client 发送 `/v1/responses`，upstream 期望 `/responses`）
- [ ] 处理请求体编码（zstd、gzip 等）
- [ ] 处理响应流（SSE events、chunked transfer）
- [ ] **若使用 reverse proxy，将传输固定为 HTTP** - 禁用会绕过 HTTP proxy 的 WebSocket/gRPC 特性
- [ ] 用真实流量验证（不仅是 unit tests）

### 验证检查点

```bash
# Run the client through claude-tap
uv run python -m claude_tap --tap-client <name> -- <simple prompt>

# Check trace file has actual API calls (not just /models)
wc -l .traces/trace_*.jsonl  # Should be > 1
python3 -c "
import json
with open('.traces/trace_<latest>.jsonl') as f:
    for line in f:
        d = json.loads(line)
        print(d['request']['path'], d['response']['status'])
"
```

**如果 trace 只有 1 行（models/health check），说明真实 API 调用绕过了你的 proxy。** 立即停止并排查传输路径。

---

## 第 3 阶段：Viewer 兼容性 - 每种 API 格式都不同

每个 LLM provider 的响应格式都不同。需要映射这些字段：

| Concept | Claude (Chat Completions) | OpenAI (Responses API) | Your Client |
|---------|--------------------------|----------------------|-------------|
| System prompt | `body.system` | `body.instructions` | ? |
| Messages | `body.messages[]` | `body.input[]` | ? |
| Message content | `{type: "text", text}` | `{type: "input_text", text}` | ? |
| Token usage | `response.body.usage` | SSE `response.completed` event | ? |
| Response output | `response.body.content` | SSE `response.output_text.delta` | ? |
| Tools | `body.tools[]` | `body.tools[]` | ? |

### Viewer 修复模式

1. 找出 viewer 读取 Claude 特定字段的所有位置
2. 新增 normalize 函数，把新格式映射到通用结构
3. 保持向后兼容，旧 trace 仍要能正确渲染

### 验证检查点

用真实 trace 打开 HTML viewer 并验证：
- [ ] System prompt 已显示
- [ ] Messages 以正确 role 渲染
- [ ] Token 计数非零
- [ ] Turn 间 diff 可用
- [ ] Response output 已显示

---

## 第 4 阶段：录制 - 证据是交付物的一部分

每次新增 client 集成都需要：

1. **终端录制**：展示 client 通过 claude-tap 运行的真实 E2E 会话
   - 工具：`asciinema rec` → `agg`（GIF）→ `ffmpeg`（MP4）
   - 必须展示：启动 banner、至少 2-3 轮对话、如适用的 tool calls

2. **Viewer 录制**：通过 Playwright 自动化演示 HTML viewer
   - 工具：Playwright `record_video_dir` → `.webm` → `ffmpeg`（MP4）
   - 必须展示：system prompt、messages、tokens、diff

3. **截图**：用于 PR review 的静态证据
   - 至少包括：overview、messages、diff、token stats

**使用真实 trace 数据，不要用 mock。** reviewer 一眼就能看出来。

### PR 截图陷阱

PR 描述中使用绝对 URL（`raw.githubusercontent.com/...`），不要用相对路径。
GitHub PR 正文不会解析相对图片路径。

---

## 第 5 阶段：防御式设计 - 下一次会坏在哪里？

集成跑通后，提前预判未来的破坏点：

- **Client 更新传输方式**：显式固定传输，不要假设永远是 HTTP。
  记录 workaround，并为原生支持创建 TODO。
- **Client 修改 API 格式**：将 normalize 函数隔离，便于后续更新。
- **Client 新增 auth 复杂性**：在 plan 文档记录所需 scopes/permissions。

### 始终留下这些资产

- [ ] 遇到非显而易见问题时，写 error experience 文档
- [ ] 已知限制写 TODO/plan 文档（例如 WebSocket 原生支持）
- [ ] 补能覆盖你发现的特定失败模式的测试

---

## 反模式（来自 Codex 集成）

| Anti-pattern | 发生了什么 | 经验 |
|-------------|-----------|------|
| 相信“看起来明显”的 API endpoint | 以为是 `api.openai.com`，实际是 `chatgpt.com/backend-api/codex` | 必须追踪真实网络调用 |
| 假设传输一定是 HTTP | Codex 默认使用 WebSocket，proxy 什么都看不到 | 验证 wire transport，必要时固定 |
| 在数据修复前录制演示 | 首版录制中 token 为 0（trace 中有 403 错误） | 先修数据链路，最后再录制 |
| 截图使用旧 trace 数据 | 截图仍显示修复前 trace 的错误 | 修复后必须重新录制 |
| PR 正文使用相对图片路径 | GitHub 上所有图片都坏掉 | 使用 `raw.githubusercontent.com` 绝对 URL |

---

## 模板：新 Client Checklist

```markdown
## Adding support for: <CLIENT_NAME>

### Recon
- [ ] Identified API endpoint(s)
- [ ] Identified transport (HTTP/WS/gRPC)
- [ ] Identified env var for base URL
- [ ] Verified child process inherits env var
- [ ] Identified request encoding

### Proxy
- [ ] Env var injection working
- [ ] Path mapping correct
- [ ] Request decompression working
- [ ] Response streaming captured
- [ ] Transport pinned to HTTP (if reverse proxy)
- [ ] Trace file has real API calls (not just models)

### Viewer
- [ ] System prompt displayed
- [ ] Messages rendered
- [ ] Token counts non-zero
- [ ] Diff working
- [ ] Response output shown
- [ ] Old Claude traces still work

### Evidence
- [ ] Terminal recording (≥3 turns)
- [ ] Viewer recording
- [ ] Screenshots (≥5)
- [ ] PR description with absolute image URLs

### Docs
- [ ] Error experience (if applicable)
- [ ] TODO for known limitations
- [ ] Checklist template used
```
