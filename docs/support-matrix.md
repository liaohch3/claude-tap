---
owner: claude-tap-maintainers
last_reviewed: 2026-05-09
source_of_truth: AGENTS.md
---

# Support Matrix

This document tracks all verified (client × auth × target × transport) combinations.
**Any proxy/routing change must verify all applicable rows before merge.**

Simplified Chinese version: [支持矩阵](support-matrix.zh.md).

## Client Configurations

| Client | Auth Mode | Target | strip_path_prefix | Transport | Status |
|--------|-----------|--------|-------------------|-----------|--------|
| Claude Code | API Key | `https://api.anthropic.com` | none | HTTP/SSE | Verified |
| Codex CLI | API Key (`OPENAI_API_KEY`) | `https://api.openai.com` | none | HTTP/SSE | Verified |
| Codex CLI | API Key (`OPENAI_API_KEY`) | `https://api.openai.com` | none | WebSocket | Verified |
| Codex CLI | OAuth (`codex login`) | `https://chatgpt.com/backend-api/codex` | `/v1` | HTTP/SSE | Verified |
| Codex CLI | OAuth (`codex login`) | `https://chatgpt.com/backend-api/codex` | `/v1` | WebSocket | Verified |
| Kimi CLI | Kimi CLI auth/config | `https://api.kimi.com/coding/v1` | none | HTTP/SSE Chat Completions | Unit-tested |
| Kimi CLI | Kimi CLI auth/config | `https://api.moonshot.ai/v1` | none | HTTP/SSE Chat Completions | Supported by config |
| OpenCode | Provider creds via `opencode providers` | Forward proxy (any HTTPS upstream) | n/a | HTTP/SSE | Unit-tested |
| OpenCode | Anthropic provider only (`--tap-proxy-mode reverse`) | `https://api.anthropic.com` | none | HTTP/SSE | Unit-tested |
| Hermes Agent | Provider creds via `~/.hermes/` | Forward proxy (any HTTPS upstream) | n/a | HTTP/SSE | Unit-tested |
| Hermes Agent | Custom OpenAI-compatible provider (`--tap-proxy-mode reverse`) | `https://api.openai.com` | `/v1` | HTTP/SSE | Unit-tested |
| Cursor CLI | Cursor login (`cursor-agent login`) | Forward proxy to `https://api2.cursor.sh` | n/a | HTTPS/protobuf + local transcript import | Real E2E verified |

## Default Proxy Mode by Client

Each client in `CLIENT_CONFIGS` declares a `default_proxy_mode` used when
`--tap-proxy-mode` is omitted:

| Client | Default mode | Reason |
|--------|--------------|--------|
| `claude` | `reverse` | Single provider, native `ANTHROPIC_BASE_URL` env var |
| `codex` | `reverse` | Single provider, native `OPENAI_BASE_URL` env var |
| `kimi` | `reverse` | Single provider, native `KIMI_BASE_URL` env var |
| `opencode` | `forward` | Multi-provider; forward proxy captures every upstream regardless of which env var the client honors |
| `hermes` | `forward` | Multi-provider Python agent; `httpx` and `requests` honor `HTTPS_PROXY` natively, so forward proxy capture is the natural default |
| `cursor` | `forward` | Cursor CLI has no base URL override; forward proxy captures network traffic and local transcripts provide readable turns |

Users can always override with `--tap-proxy-mode {reverse,forward}`.

## Subcommand Argv Rewrites

Some clients delegate to OS service managers (launchd / systemd / schtasks) for
their long-running daemons. The spawned daemon does **not** inherit the
proxy / CA env we inject, so trace capture would silently fail. claude-tap
detects these patterns and rewrites the argv to the foreground equivalent:

| Client | Detected argv | Rewritten to | Reason |
|--------|---------------|--------------|--------|
| `hermes` | `gateway start [...]` | `gateway run [...]` | Recent hermes versions delegate `gateway start` to systemd / launchd; `gateway run` is the foreground equivalent and is exactly what the systemd unit's `ExecStart=` itself invokes. |

The rewrite is logged loudly at process start so users can spot it and pass
`--tap-no-launch` + run the original command themselves if they actually want
the daemonised behaviour (and accept that no traffic will be captured).

> **Note:** Gateway mode only produces traces when a configured messaging platform (Slack, Telegram, etc.)
> delivers a message to the bot. Without an active platform integration, the gateway makes no LLM calls
> and no traces are recorded. Use TUI mode (`claude-tap --tap-client hermes`) for local trace capture.

## URL Construction Rules

The proxy constructs upstream URLs as: `target + forwarded_path`

When `strip_path_prefix` is set, the prefix is removed from the incoming path before forwarding:

```
incoming: /v1/responses
strip:    /v1
result:   /responses
upstream: {target}/responses
```

### Decision Logic

```python
strip = CLIENT_CONFIGS[client].reverse_strip_path_prefix(target)
```

| Target contains `api.openai.com` | strip | Example |
|----------------------------------|-------|---------|
| Yes | none | `/v1/responses` → `api.openai.com/v1/responses` |
| No | `/v1` | `/v1/responses` → `chatgpt.com/.../responses` |

## Verification Methods

### Automated (CI)

- `test_codex_upstream_url_construction` — verifies URL construction for all 5 matrix combinations
- `test_codex_client_reverse_proxy` — e2e with fake upstream (OAuth-like, with strip)
- `test_kimi_registered_in_client_configs` — verifies Kimi CLI registration
- `test_kimi_client_reverse_proxy` — e2e with fake Kimi Chat Completions stream
- `test_chat_completions_reasoning_content_is_mirrored_as_thinking` — verifies Kimi thinking stream rendering shape
- `test_websocket_proxy_basic` — WS relay and trace recording
- `test_hermes_*` — registration, parse_args default-mode resolution, forward/reverse env, argv rewrite
- `test_cursor_registered_in_client_configs` — verifies Cursor CLI registration and default forward mode
- `test_run_client_cursor_forward_sets_proxy_ca_and_no_proxy` — verifies Cursor launch env for forward proxy mode
- `test_import_cursor_transcripts_appends_viewer_friendly_records` — verifies readable Cursor transcript import
- `test_import_cursor_transcripts_preserves_tool_uses` — verifies Cursor tool_use blocks render in the viewer trace shape

### Manual (pre-merge for proxy changes)

```bash
# API Key mode
uv run python -m claude_tap --tap-client codex --tap-no-launch --tap-port 0
# Verify log shows correct upstream URL

# OAuth mode
uv run python -m claude_tap --tap-client codex \
  --tap-target https://chatgpt.com/backend-api/codex --tap-no-launch --tap-port 0
# Verify log shows correct upstream URL

# Cursor CLI
uv run python -m claude_tap --tap-client cursor -- -p --trust --model auto "Reply OK"
# Verify the trace contains raw proxy records plus cursor-transcript records

# Kimi CLI
uv run python -m claude_tap --tap-client kimi -- --thinking
# Verify the trace contains /chat/completions records and thinking/text output
```

### Real E2E (optional, when auth is available)

```bash
# tmux-based real verification
tmux new-session -d -s verify \
  "uv run python -m claude_tap --tap-client codex --tap-target TARGET --tap-no-launch --tap-port 8080"
# In another window:
OPENAI_BASE_URL=http://127.0.0.1:8080/v1 codex exec "Reply: OK"
```

```bash
# Cursor CLI real verification
uv run python -m claude_tap --tap-client cursor -- -p --trust --model auto \
  "Use tools to inspect the workspace and reply OK"
# Verify the generated HTML contains cursor-transcript turns and tool_use blocks.
```

## Adding New Clients or Backends

When adding a new client or backend:

1. Add a row to the matrix above
2. Add a `CLIENT_CONFIGS` entry and a launch/config test
3. Add an e2e test with fake upstream if applicable
4. Verify with real E2E if auth is available
5. Update the public docs in both English and Simplified Chinese (`README.md` plus `README_zh.md`, and matching `docs/guides/*.md` plus `docs/guides/*.zh.md` guide files when applicable)
