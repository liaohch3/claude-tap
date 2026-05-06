---
name: openclaw-claude-tap-setup
description: Configure and launch claude-tap as a local proxy for OpenClaw integration. Use when setting up claude-tap with OpenClaw, troubleshooting API proxy issues, or managing trace collection in an OpenClaw environment.
---

# Claude Tap 设置指南（OpenClaw 集成必读）

English version: [Claude Tap Setup Guide](OPENCLAW_README.md).

## 背景

OpenClaw 可以通过 claude-tap 这样的本地代理 trace 模型 API 请求。配置完成后，发往指定模型 provider 的所有 API 请求都会先转发到本地代理，再由本地代理转发到上游端点，同时记录请求和响应用于调试。

**重要：把 openclaw.json 中 provider 的 `baseUrl` 指向本地代理后，必须确保代理进程正在运行。否则 API 请求会 connection refused，导致整个 OpenClaw 服务不可用。**

## 配置

### 1. openclaw.json 中的 claude-tap 配置

在 `~/.openclaw/openclaw.json` 的 `models.providers.<provider>` 下增加指向本地代理的 `baseUrl`。Anthropic 示例：

```json
{
  "models": {
    "providers": {
      "anthropic": {
        "baseUrl": "http://127.0.0.1:8787",
        "api": "anthropic-messages",
        "models": [
          {
            "id": "claude-sonnet-4-6",
            "name": "Claude Sonnet 4.6 (via claude-tap)",
            "api": "anthropic-messages",
            "reasoning": false,
            "input": ["text", "image"],
            "cost": {
              "input": 0,
              "output": 0,
              "cacheRead": 0,
              "cacheWrite": 0
            },
            "contextWindow": 200000,
            "maxTokens": 8192
          }
        ]
      }
    }
  }
}
```

**关键字段：**

- `baseUrl`：必须指向 claude-tap 监听的地址和端口，默认是 `http://127.0.0.1:8787`
- 其他字段：按需配置模型参数

### 2. 启动 claude-tap

配置 `openclaw.json` 后，**必须先启动 claude-tap，再启动 OpenClaw**。

#### 仅 API 代理（无前端）

```bash
nohup claude-tap \
  --tap-port 8787 \
  --tap-host 127.0.0.1 \
  --tap-no-launch \
  --tap-no-open \
  > ~/.openclaw/logs/claude-tap.log 2>&1 &
```

#### API 代理 + 实时查看器前端

```bash
nohup claude-tap \
  --tap-port 8787 \
  --tap-host 127.0.0.1 \
  --tap-no-launch \
  --tap-no-open \
  --tap-live \
  --tap-live-port 8788 \
  > ~/.openclaw/logs/claude-tap.log 2>&1 &
```

**参数说明：**

| 参数 | 说明 |
|------|------|
| `--tap-port 8787` | API 代理监听端口；必须与 openclaw.json `baseUrl` 中的端口一致 |
| `--tap-host 127.0.0.1` | 绑定到 loopback 地址；仅允许本地访问，不暴露到网络 |
| `--tap-no-launch` | 只启动代理；不启动 Claude CLI 客户端 |
| `--tap-no-open` | 退出时不自动打开 HTML 报告 |
| `--tap-live` | 启用实时 trace viewer 前端 |
| `--tap-live-port 8788` | 实时 viewer 前端端口 |

**安全提示：必须指定 `--tap-host 127.0.0.1`，确保代理和前端都只监听 loopback 地址。若不指定该参数，`--tap-no-launch` 模式默认绑定 `0.0.0.0`，会把端口暴露到公网。**

### 3. 验证 claude-tap 正在运行

```bash
# 检查端口是否监听
ss -tlnp | grep -E "8787|8788"

# 期望输出（两个端口都应绑定到 127.0.0.1 且处于 LISTEN 状态）：
# LISTEN  0  128  127.0.0.1:8787  0.0.0.0:*  users:(("claude-tap",...))
# LISTEN  0  128  127.0.0.1:8788  0.0.0.0:*  users:(("claude-tap",...))
# 如果看到 0.0.0.0:8787，说明端口已公开暴露；立即停止
# 并使用 --tap-host 127.0.0.1 重新启动

# 测试代理连通性
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8787/
# 404 表示代理正在运行（根路径没有 handler 是预期行为）

# 检查进程
ps aux | grep claude-tap | grep -v grep
```

### 4. 访问实时查看器前端

因为绑定到 `127.0.0.1`，实时 viewer 只能从本机访问：`http://127.0.0.1:8788`

远程访问请使用 SSH 端口转发：

```bash
ssh -L 8788:127.0.0.1:8788 user@remote-host
```

然后在本地浏览器打开 `http://127.0.0.1:8788`。

## 启动 / 重启顺序（关键）

```text
1. 启动 claude-tap（端口 8787）
2. 验证端口正在监听（ss -tlnp | grep 8787，确认 LISTEN 状态）
3. 验证代理连通性（curl http://127.0.0.1:8787/，期望 404）
4. 以上两项都通过后，再重启 OpenClaw gateway
```

**绝不要在 claude-tap 未就绪时重启 gateway。重启 gateway 前必须确认 claude-tap 端口正在监听，并且代理可达。**

**如果顺序反了、代理没启动，或跳过验证，所有通过代理路由的模型 API 调用都会失败（connection refused），实际效果就是服务不可用。**

## 故障排查

### API 服务不可用 / 无法调用模型

1. 检查 claude-tap 进程是否存在：`ps aux | grep claude-tap`
2. 检查端口是否监听：`ss -tlnp | grep 8787`
3. 如果未运行，按上面的步骤启动 claude-tap
4. 重启 OpenClaw gateway

### 移除 claude-tap

从 `~/.openclaw/openclaw.json` 删除 `baseUrl` 字段，或删除整个自定义 provider block，让 OpenClaw 直接连接 provider 官方 API。然后重启 gateway。

## 日志

- claude-tap 日志：`~/.openclaw/logs/claude-tap.log`
- Trace 文件默认保存到：`./.traces/`
