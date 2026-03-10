---
name: openclaw-claude-tap-setup
description: Configure and launch claude-tap as a local proxy for OpenClaw integration. Use when setting up claude-tap with OpenClaw, troubleshooting API proxy issues, or managing trace collection in an OpenClaw environment.
---

# Claude Tap Setup Guide (Required Reading for OpenClaw Integration)

## Background

OpenClaw can trace model API requests through a local proxy such as claude-tap. Once configured, all API requests destined for a given model provider are forwarded through the local proxy to the upstream endpoint, while requests and responses are recorded for debugging.

**Important: After pointing a provider's `baseUrl` in openclaw.json to the local proxy, you MUST ensure the proxy process is running. Otherwise, API requests will fail with connection refused, bringing down the entire OpenClaw service.**

## Configuration

### 1. claude-tap Configuration in openclaw.json

Add a `baseUrl` pointing to the local proxy under `models.providers.<provider>` in `~/.openclaw/openclaw.json`. Example for Anthropic:

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

**Key fields:**
- `baseUrl`: Must point to the address and port claude-tap listens on (default `http://127.0.0.1:8787`)
- Other fields: Configure model parameters as needed

### 2. Starting claude-tap

After configuring `openclaw.json`, **you MUST start claude-tap before starting OpenClaw**.

#### API Proxy Only (No Frontend)

```bash
nohup claude-tap \
  --tap-port 8787 \
  --tap-host 127.0.0.1 \
  --tap-no-launch \
  --tap-no-open \
  > ~/.openclaw/logs/claude-tap.log 2>&1 &
```

#### API Proxy + Live Viewer Frontend

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

**Parameter Reference:**

| Parameter | Description |
|-----------|-------------|
| `--tap-port 8787` | API proxy listen port; must match the port in openclaw.json `baseUrl` |
| `--tap-host 127.0.0.1` | Bind to loopback address; local access only, not exposed to the network |
| `--tap-no-launch` | Start the proxy only; do not launch the Claude CLI client |
| `--tap-no-open` | Do not auto-open the HTML report on exit |
| `--tap-live` | Enable the live trace viewer frontend |
| `--tap-live-port 8788` | Live Viewer frontend port |

**Security Notice: You MUST specify `--tap-host 127.0.0.1` to ensure both the proxy and frontend listen only on the loopback address. Without this flag, `--tap-no-launch` mode defaults to binding `0.0.0.0`, exposing the port to the public network.**

### 3. Verifying claude-tap Is Running

```bash
# Check whether the ports are listening
ss -tlnp | grep -E "8787|8788"

# Expected output (both ports should be bound to 127.0.0.1 and in LISTEN state):
# LISTEN  0  128  127.0.0.1:8787  0.0.0.0:*  users:(("claude-tap",...))
# LISTEN  0  128  127.0.0.1:8788  0.0.0.0:*  users:(("claude-tap",...))
# If you see 0.0.0.0:8787, the port is publicly exposed — stop immediately
# and restart with --tap-host 127.0.0.1

# Test proxy connectivity
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8787/
# A 404 response means the proxy is running (no handler on the root path is expected)

# Check the process
ps aux | grep claude-tap | grep -v grep
```

### 4. Accessing the Live Viewer Frontend

Because it is bound to 127.0.0.1, the Live Viewer is accessible only from the local machine: `http://127.0.0.1:8788`

For remote access, use SSH port forwarding:
```bash
ssh -L 8788:127.0.0.1:8788 user@remote-host
```
Then open `http://127.0.0.1:8788` in your local browser.

## Startup / Restart Order (Critical)

```
1. Start claude-tap (port 8787)
2. Verify the port is listening (ss -tlnp | grep 8787 — confirm LISTEN state)
3. Verify proxy connectivity (curl http://127.0.0.1:8787/ — expect 404)
4. Only after both checks pass, restart the OpenClaw gateway
```

**NEVER restart the gateway while claude-tap is not ready. You must confirm that the claude-tap port is listening and the proxy is reachable before restarting the gateway.**

**If the order is reversed, the proxy is not started, or verification is skipped, all model API calls routed through the proxy will fail (connection refused), effectively bringing the service down.**

## Troubleshooting

### API Service Down / Cannot Call Models

1. Check whether the claude-tap process exists: `ps aux | grep claude-tap`
2. Check whether the port is listening: `ss -tlnp | grep 8787`
3. If not running, start claude-tap following the steps above
4. Restart the OpenClaw gateway

### Removing claude-tap

Delete the `baseUrl` field (or the entire custom provider block) from `~/.openclaw/openclaw.json` so that OpenClaw connects directly to the provider's official API. Then restart the gateway.

## Logs

- claude-tap log: `~/.openclaw/logs/claude-tap.log`
- Trace files are saved by default in: `./.traces/`
