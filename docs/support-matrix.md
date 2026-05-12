---
owner: claude-tap-maintainers
last_reviewed: 2026-05-12
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
| Gemini CLI | Google / Gemini / Vertex auth | Forward proxy (any HTTPS upstream) | n/a | HTTP/SSE | Unit-tested |
| Gemini CLI | Gemini API key (`--tap-proxy-mode reverse`) | `https://generativelanguage.googleapis.com` | none | HTTP/SSE GenerateContent | Unit-tested |
| Gemini CLI | Vertex AI (`--tap-proxy-mode reverse`) | `https://aiplatform.googleapis.com` | none | HTTP/SSE GenerateContent | Supported by config |
| Kimi CLI | Kimi CLI auth/config | `https://api.kimi.com/coding/v1` | none | HTTP/SSE Chat Completions | Unit-tested |
| Kimi CLI | Kimi CLI auth/config | `https://api.moonshot.ai/v1` | none | HTTP/SSE Chat Completions | Supported by config |
| OpenCode | Provider creds via `opencode providers` | Forward proxy (any HTTPS upstream) | n/a | HTTP/SSE | Unit-tested |
| OpenCode | Anthropic provider only (`--tap-proxy-mode reverse`) | `https://api.anthropic.com` | none | HTTP/SSE | Unit-tested |
| Pi | Provider creds via Pi config/API keys | Forward proxy (any HTTPS upstream) | n/a | HTTP/SSE | Unit-tested |
| Pi | OpenAI-compatible provider (`--tap-proxy-mode reverse`) | `https://api.openai.com` | none | HTTP/SSE Chat Completions | Unit-tested |
| iFlow CLI | iFlow auth/API key | `https://apis.iflow.cn` + `/v1` path | none | HTTP/SSE Chat Completions | Unit-tested |
| iFlow CLI | OpenAI-compatible provider override | user-provided `--tap-target` | none | HTTP/SSE Chat Completions | Supported by config |
| Hermes Agent | Provider creds via `~/.hermes/` | Forward proxy (any HTTPS upstream) | n/a | HTTP/SSE | Unit-tested |
| Hermes Agent | Custom OpenAI-compatible provider (`--tap-proxy-mode reverse`) | `https://api.openai.com` | `/v1` | HTTP/SSE | Unit-tested |
| Cursor CLI | Cursor login (`cursor-agent login`) | Forward proxy to `https://api2.cursor.sh` | n/a | HTTPS/protobuf + local transcript import | Real E2E verified |
| Qoder CLI | Qoder login | Forward proxy (Qoder account upstreams) | n/a | HTTPS | Unit-tested |
| Devin CLI | Devin auth | Forward proxy (Devin cloud upstreams) | n/a | HTTPS | Unit-tested |

## Default Proxy Mode by Client

Each client in `CLIENT_CONFIGS` declares a `default_proxy_mode` used when
`--tap-proxy-mode` is omitted:

| Client | Default mode | Reason |
|--------|--------------|--------|
| `claude` | `reverse` | Single provider, native `ANTHROPIC_BASE_URL` env var |
| `codex` | `reverse` | Single provider, native `OPENAI_BASE_URL` env var |
| `gemini` | `forward` | Multiple Google auth/upstream modes; forward proxy captures Google account, Gemini API key, and Vertex flows without guessing target |
| `kimi` | `reverse` | Single provider, native `KIMI_BASE_URL` env var |
| `opencode` | `forward` | Multi-provider; forward proxy captures every upstream regardless of which env var the client honors |
| `pi` | `forward` | Multi-provider; forward proxy captures the selected provider without assuming a provider-specific base URL |
| `iflow` | `reverse` | OpenAI-compatible native base URL config via `IFLOW_baseUrl` / `IFLOW_BASE_URL` |
| `hermes` | `forward` | Multi-provider Python agent; `httpx` and `requests` honor `HTTPS_PROXY` natively, so forward proxy capture is the natural default |
| `cursor` | `forward` | Cursor CLI has no base URL override; forward proxy captures network traffic and local transcripts provide readable turns |
| `qoder` | `forward` | Account-backed CLI with no documented provider base URL override |
| `devin` | `forward` | Devin cloud integration documents proxy-env use; forward proxy is the compatible default |

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
- `test_all_requested_clients_are_registered` — verifies every supported CLI key is present
- `test_client_binary_names_match_official_install_packages` — verifies command names (`gemini`, `pi`, `iflow`, `qodercli`, `devin`, etc.)
- `test_parse_args_accepts_requested_clients_and_resolves_default_modes` — verifies parse-time target and default proxy-mode resolution for all clients
- `test_run_client_reverse_sets_all_configured_base_url_envs` — verifies reverse-mode env injection for all clients, including multi-env Gemini/iFlow config
- `test_run_client_forward_sets_proxy_and_generic_ca_envs` — verifies forward-mode proxy and CA env injection for all clients
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

# Gemini CLI
uv run python -m claude_tap --tap-client gemini -- -p "Reply OK"
# Verify the trace contains Google Gemini/Vertex API records

# Pi
uv run python -m claude_tap --tap-client pi -- -p "Reply OK"
# Verify the trace contains the selected provider records

# iFlow CLI
uv run python -m claude_tap --tap-client iflow -- -p "Reply OK"
# Verify the trace contains /v1/chat/completions records

# Qoder CLI
uv run python -m claude_tap --tap-client qoder -- -p "Reply OK"
# Verify the trace contains Qoder account-backed records

# Devin CLI
uv run python -m claude_tap --tap-client devin -- -p "Reply OK"
# Verify the trace contains Devin cloud records
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
