# Claude Tap 配置与启动指南（OpenClaw 集成）

## 背景

OpenClaw 可以通过本地代理（如 claude-tap）来 trace 模型 API 请求。配置后，所有发往对应模型 provider 的 API 请求会经过本地代理转发到上游，同时记录请求/响应用于调试。

**重要：在 openclaw.json 中将某个 provider 的 `baseUrl` 指向本地代理后，必须确保该代理进程在运行，否则 API 请求会因连接拒绝而失败，导致整个 OpenClaw 服务挂掉。**

## 配置说明

### 1. openclaw.json 中的 claude-tap 配置

在 `~/.openclaw/openclaw.json` 的 `models.providers.<provider名>` 中添加 `baseUrl` 指向本地代理。以 anthropic 为例：

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
- `baseUrl`: 必须指向 claude-tap 监听的地址和端口（默认 `http://127.0.0.1:8787`）
- 其他字段按需配置模型参数

### 2. 启动 claude-tap

配置完 `openclaw.json` 后，**必须先启动 claude-tap，再启动 OpenClaw**。

#### 仅启动 API 代理（无前端）

```bash
nohup claude-tap \
  --tap-port 8787 \
  --tap-host 127.0.0.1 \
  --tap-no-launch \
  --tap-no-open \
  > ~/.openclaw/logs/claude-tap.log 2>&1 &
```

#### 启动 API 代理 + Live Viewer 前端

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
| `--tap-port 8787` | API 代理监听端口，需与 openclaw.json 中 baseUrl 的端口一致 |
| `--tap-host 127.0.0.1` | 绑定到本地回环地址，仅允许本机访问，不暴露到公网 |
| `--tap-no-launch` | 仅启动代理，不启动 Claude CLI 客户端 |
| `--tap-no-open` | 退出时不自动打开 HTML 报告 |
| `--tap-live` | 启动实时 trace 查看前端 |
| `--tap-live-port 8788` | Live Viewer 前端端口 |

**安全注意：必须指定 `--tap-host 127.0.0.1`，确保代理和前端仅监听本地回环地址，不暴露到公网。不指定此参数时，`--tap-no-launch` 模式下默认绑定 `0.0.0.0`，会导致端口对外开放。**

### 3. 验证 claude-tap 是否正常运行

```bash
# 检查端口是否在监听
ss -tlnp | grep -E "8787|8788"

# 预期输出（两个端口都应该绑定 127.0.0.1 并 LISTEN）:
# LISTEN  0  128  127.0.0.1:8787  0.0.0.0:*  users:(("claude-tap",...))
# LISTEN  0  128  127.0.0.1:8788  0.0.0.0:*  users:(("claude-tap",...))
# 如果看到 0.0.0.0:8787 说明绑定了公网，需要立即停掉并加 --tap-host 127.0.0.1 重启

# 测试代理连通性
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8787/
# 返回 404 表示代理正常运行（根路径无 handler 是正常的）

# 检查进程
ps aux | grep claude-tap | grep -v grep
```

### 4. 访问 Live Viewer 前端

由于绑定了 127.0.0.1，Live Viewer 仅限本机访问：`http://127.0.0.1:8788`

如需远程访问，可通过 SSH 端口转发：
```bash
ssh -L 8788:127.0.0.1:8788 user@remote-host
```
然后在本地浏览器打开 `http://127.0.0.1:8788`。

## 启动/重启顺序（重要）

```
1. 启动 claude-tap（端口 8787）
2. 验证 claude-tap 端口已监听（ss -tlnp | grep 8787 确认 LISTEN 状态）
3. 验证代理连通性（curl http://127.0.0.1:8787/ 返回 404 即正常）
4. 确认以上两步全部通过后，再重启 OpenClaw gateway
```

**严禁在 claude-tap 未就绪的情况下重启 gateway。必须逐项确认 claude-tap 端口监听正常、代理可连通之后，才能执行 gateway 重启操作。**

**如果颠倒顺序、忘记启动代理、或未验证就重启 gateway，OpenClaw 中所有通过该代理转发的模型 API 调用都会失败（connection refused），表现为服务挂掉。**

## 故障排查

### API 服务挂掉 / 无法调用模型

1. 检查 claude-tap 进程是否存在：`ps aux | grep claude-tap`
2. 检查端口是否监听：`ss -tlnp | grep 8787`
3. 如果没运行，按上面步骤启动 claude-tap
4. 重启 OpenClaw gateway

### 不想用 claude-tap 了

从 `~/.openclaw/openclaw.json` 中删除对应 provider 的 `baseUrl` 字段（或整个自定义 provider 配置块），让 OpenClaw 直连该 provider 的官方 API。然后重启 gateway。

## 日志

- claude-tap 日志：`~/.openclaw/logs/claude-tap.log`
- trace 文件默认保存在：`./.traces/` 目录
