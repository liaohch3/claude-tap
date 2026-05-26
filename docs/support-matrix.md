---
owner: claude-tap-maintainers
last_reviewed: 2026-05-20
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
| Claude Code | Claude-compatible gateway (`ANTHROPIC_BASE_URL` env or Claude settings) | Custom Anthropic-compatible upstream | none | HTTP/SSE | Unit-tested; DeepSeek real E2E verified |
| Codex CLI | API Key (`OPENAI_API_KEY`) | `https://api.openai.com` | none | HTTP/SSE | Verified |
| Codex CLI | API Key (`OPENAI_API_KEY`) | `https://api.openai.com` | none | WebSocket | Verified |
| Codex CLI | OAuth (`codex login`) | `https://chatgpt.com/backend-api/codex` | `/v1` | HTTP/SSE | Verified |
| Codex CLI | OAuth (`codex login`) | `https://chatgpt.com/backend-api/codex` | `/v1` | WebSocket | Verified |
| Gemini CLI | Google OAuth / Code Assist | Forward proxy (Google endpoints) | n/a | HTTP/SSE | Real E2E verified |
| Gemini CLI | API key / Vertex-compatible config (`--tap-proxy-mode reverse`) | `https://generativelanguage.googleapis.com` | none | HTTP/SSE | Unit-tested |
| Kimi CLI | Kimi CLI auth/config | `https://api.kimi.com/coding/v1` | none | HTTP/SSE Chat Completions | Unit-tested |
| Kimi CLI | Kimi CLI auth/config | `https://api.moonshot.ai/v1` | none | HTTP/SSE Chat Completions | Supported by config |
| OpenCode | Provider creds via `opencode providers` (OpenAI OAuth and OpenCode free provider verified) | Forward proxy (any HTTPS upstream) | n/a | HTTP/SSE | Real E2E verified |
| OpenCode | Anthropic provider only (`--tap-proxy-mode reverse`) | `https://api.anthropic.com` | none | HTTP/SSE | Unit-tested |
| Pi | Provider creds via Pi `/login` or `PI_CODING_AGENT_DIR` auth file (`openai-codex` OAuth verified) | Forward proxy (any HTTPS upstream) | n/a | HTTP/SSE + WebSocket | Real E2E verified |
| Pi | Custom OpenAI-compatible setup (`--tap-proxy-mode reverse`) | `https://api.openai.com` | none | HTTP/SSE | Unit-tested |
| Hermes Agent | Provider creds via `~/.hermes/` | Forward proxy (any HTTPS upstream) | n/a | HTTP/SSE | Unit-tested |
| Hermes Agent | Custom OpenAI-compatible provider (`--tap-proxy-mode reverse`) | `https://api.openai.com` | `/v1` | HTTP/SSE | Unit-tested |
| Cursor CLI | Cursor login (`cursor-agent login`) | Forward proxy to `https://api2.cursor.sh` | n/a | HTTPS/protobuf + local transcript import | Real E2E verified |
| Qoder CLI | Qoder login / `QODER_PERSONAL_ACCESS_TOKEN` / `QODER_JOB_TOKEN` | Forward proxy (Qoder endpoints) | n/a | HTTP/SSE | Real E2E verified |
| Antigravity CLI | Antigravity login | Forward proxy + `CLOUD_CODE_URL` bridge to `https://daily-cloudcode-pa.googleapis.com` | `CLOUD_CODE_URL` | HTTP/SSE | Manual E2E verified; launch env, Code Assist bridge, and automatic macOS user-keychain CA trust are unit-tested |
| CodeBuddy CLI | CodeBuddy login (iOA / WeChat / Google-Github / Enterprise Domain) | Auto-detected from `~/.codebuddy/local_storage/` cache; default `https://copilot.tencent.com/v2` | `CODEBUDDY_BASE_URL` | HTTP/SSE Chat Completions | Real E2E verified on iOA |

## Default Proxy Mode by Client

Each client in `CLIENT_CONFIGS` declares a `default_proxy_mode` used when
`--tap-proxy-mode` is omitted:

| Client | Default mode | Reason |
|--------|--------------|--------|
| `claude` | `reverse` | Single provider, native `ANTHROPIC_BASE_URL` env var |
| `codex` | `reverse` | Single provider, native `OPENAI_BASE_URL` env var |
| `gemini` | `forward` | Google OAuth / Code Assist uses several Google endpoints; forward proxy captures the flow without assuming a single base URL |
| `kimi` | `reverse` | Single provider, native `KIMI_BASE_URL` env var |
| `opencode` | `forward` | Multi-provider; forward proxy captures every upstream regardless of which env var the client honors |
| `pi` | `forward` | Multi-provider; Pi can use OpenAI Codex OAuth and custom model registry providers, so forward proxy captures traffic without relying on a single base URL override |
| `hermes` | `forward` | Multi-provider Python agent; `httpx` and `requests` honor `HTTPS_PROXY` natively, so forward proxy capture is the natural default |
| `cursor` | `forward` | Cursor CLI has no base URL override; forward proxy captures network traffic and local transcripts provide readable turns |
| `qoder` | `forward` | Qoder CLI uses multiple Qoder service endpoints and has no reliable single base URL override |
| `agy` | `forward` | Antigravity uses multiple Google / Antigravity endpoints; claude-tap sets `HTTPS_PROXY` for auxiliary traffic and `CLOUD_CODE_URL` for Code Assist model traffic |
| `codebuddy` | `reverse` | Single provider, native `CODEBUDDY_BASE_URL` env var; supports `--settings` env injection. Endpoint auto-detected from CodeBuddy's login cache |

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
- `test_gemini_registered_in_client_configs` — verifies Gemini CLI registration and default forward mode
- `test_run_client_gemini_forward_sets_proxy_ca_and_skips_base_url_envs` — verifies Gemini forward proxy launch env
- `test_run_client_gemini_reverse_sets_both_base_url_envs` — verifies Gemini reverse proxy base URL env injection
- `test_viewer_renders_gemini_semantic_sections` — verifies Gemini systemInstruction, contents, functionDeclarations, functionCall, functionResponse, SSE output, and token usage render as semantic viewer sections
- `test_kimi_registered_in_client_configs` — verifies Kimi CLI registration
- `test_kimi_client_reverse_proxy` — e2e with fake Kimi Chat Completions stream
- `test_chat_completions_reasoning_content_is_mirrored_as_thinking` — verifies Kimi thinking stream rendering shape
- `test_websocket_proxy_basic` — WS relay and trace recording
- `test_hermes_*` — registration, parse_args default-mode resolution, forward/reverse env, argv rewrite
- `test_pi_*` — registration, parse_args default-mode resolution, forward/reverse env, and argument preservation
- `test_cursor_registered_in_client_configs` — verifies Cursor CLI registration and default forward mode
- `test_run_client_cursor_forward_sets_proxy_ca_and_no_proxy` — verifies Cursor launch env for forward proxy mode
- `test_import_cursor_transcripts_appends_viewer_friendly_records` — verifies readable Cursor transcript import
- `test_import_cursor_transcripts_preserves_tool_uses` — verifies Cursor tool_use blocks render in the viewer trace shape
- `test_qoder_*` — verifies Qoder registration, parse_args default-mode resolution, forward/reverse env, and argument preservation
- `test_parse_args_agy_does_not_require_tap_trust_ca` — verifies Antigravity uses the same launch shape as other clients
- `test_auto_ca_trust_*` — verifies Antigravity automatically requests macOS user-keychain CA trust without sudo
- `test_macos_*_ca_command_*` — verifies CA trust commands use the user login keychain and do not invoke sudo
- `test_codebuddy_*` — verifies CodeBuddy registration, parse_args default reverse mode, settings injection, forward/reverse env, target detection from `CODEBUDDY_BASE_URL` env, and the login-time endpoint cache reader

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

# Qoder CLI
uv run python -m claude_tap --tap-client qoder -- -p "Reply OK" --permission-mode dont_ask
# Verify stdout contains the assistant response and the trace contains Qoder endpoint records

# Antigravity CLI (macOS)
uv run python -m claude_tap --tap-client agy --tap-live
# On first run, verify macOS prompts only for the user login keychain, not sudo/admin System keychain writes.
# Then verify the trace contains /v1internal:streamGenerateContent model records.

# Kimi CLI
uv run python -m claude_tap --tap-client kimi -- --thinking
# Verify the trace contains /chat/completions records and thinking/text output

# Gemini CLI
uv run python -m claude_tap --tap-client gemini -- -p "Reply OK" --yolo --output-format text
# Verify the trace contains Google OAuth / Code Assist API records

# Pi
uv run python -m claude_tap --tap-client pi -- \
  --model openai-codex/gpt-5.3-codex-spark -p "Reply OK"
# Verify the trace contains chatgpt.com/backend-api records and readable OpenAI Responses sections

# CodeBuddy (auto-detected endpoint after login)
uv run python -m claude_tap --tap-client codebuddy -- -p "Reply OK"
# Verify the trace contains /v2/chat/completions records and the response body has non-zero token counts
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

```bash
# Gemini CLI real verification
uv run python -m claude_tap --tap-client gemini -- -p \
  "Use tools to inspect the workspace and reply OK" --yolo --output-format text
# Verify the trace contains cloudcode-pa.googleapis.com / streamGenerateContent records.
```

```bash
# Pi real verification with OpenAI Codex OAuth
uv run python -m claude_tap --tap-client pi -- \
  --model openai-codex/gpt-5.3-codex-spark --tools bash -p \
  "Use bash to inspect the workspace and reply OK"
# Verify the generated viewer shows Tools, System Prompt, Messages, Response,
# SSE/WebSocket events, tool calls, tool outputs, and token usage.
```

## Adding New Clients or Backends

When adding a new client or backend:

1. Add a row to the matrix above
2. Add a `CLIENT_CONFIGS` entry and a launch/config test
3. Add an e2e test with fake upstream if applicable
4. Verify with real E2E if auth is available
5. Update the public docs in both English and Simplified Chinese (`README.md` plus `README_zh.md`, and matching `docs/guides/*.md` plus `docs/guides/*.zh.md` guide files when applicable)
