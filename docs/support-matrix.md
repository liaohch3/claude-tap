---
owner: claude-tap-maintainers
last_reviewed: 2026-05-01
source_of_truth: AGENTS.md
---

# Support Matrix

This document tracks all verified (client × auth × target × transport) combinations.
**Any proxy/routing change must verify all applicable rows before merge.**

## Client Configurations

| Client | Auth Mode | Target | strip_path_prefix | Transport | Status |
|--------|-----------|--------|-------------------|-----------|--------|
| Claude Code | API Key | `https://api.anthropic.com` | none | HTTP/SSE | Verified |
| Codex CLI | API Key (`OPENAI_API_KEY`) | `https://api.openai.com` | none | HTTP/SSE | Verified |
| Codex CLI | API Key (`OPENAI_API_KEY`) | `https://api.openai.com` | none | WebSocket | Verified |
| Codex CLI | OAuth (`codex login`) | `https://chatgpt.com/backend-api/codex` | `/v1` | HTTP/SSE | Verified |
| Codex CLI | OAuth (`codex login`) | `https://chatgpt.com/backend-api/codex` | `/v1` | WebSocket | Verified |
| OpenCode | Provider creds via `opencode providers` | Forward proxy (any HTTPS upstream) | n/a | HTTP/SSE | Unit-tested |
| OpenCode | Anthropic provider only (`--tap-proxy-mode reverse`) | `https://api.anthropic.com` | none | HTTP/SSE | Unit-tested |
| Cursor CLI | Cursor login (`cursor-agent login`) | Forward proxy to `https://api2.cursor.sh` | n/a | HTTPS/protobuf + local transcript import | Real E2E verified |

## Default Proxy Mode by Client

Each client in `CLIENT_CONFIGS` declares a `default_proxy_mode` used when
`--tap-proxy-mode` is omitted:

| Client | Default mode | Reason |
|--------|--------------|--------|
| `claude` | `reverse` | Single provider, native `ANTHROPIC_BASE_URL` env var |
| `codex` | `reverse` | Single provider, native `OPENAI_BASE_URL` env var |
| `opencode` | `forward` | Multi-provider; forward proxy captures every upstream regardless of which env var the client honors |
| `cursor` | `forward` | Cursor CLI has no base URL override; forward proxy captures network traffic and local transcripts provide readable turns |

Users can always override with `--tap-proxy-mode {reverse,forward}`.

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
strip = "/v1" if client == "codex" and "api.openai.com" not in target else ""
```

| Target contains `api.openai.com` | strip | Example |
|----------------------------------|-------|---------|
| Yes | none | `/v1/responses` → `api.openai.com/v1/responses` |
| No | `/v1` | `/v1/responses` → `chatgpt.com/.../responses` |

## Verification Methods

### Automated (CI)

- `test_codex_upstream_url_construction` — verifies URL construction for all 5 matrix combinations
- `test_codex_client_reverse_proxy` — e2e with fake upstream (OAuth-like, with strip)
- `test_websocket_proxy_basic` — WS relay and trace recording
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
2. Add a URL construction test case in `test_codex_upstream_url_construction`
3. Add an e2e test with fake upstream if applicable
4. Verify with real E2E if auth is available
5. Update README.md and README_zh.md with usage examples
