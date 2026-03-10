---
owner: claude-tap-maintainers
last_reviewed: 2026-03-10
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

### Manual (pre-merge for proxy changes)

```bash
# API Key mode
uv run python -m claude_tap --tap-client codex --tap-no-launch --tap-port 0
# Verify log shows correct upstream URL

# OAuth mode
uv run python -m claude_tap --tap-client codex \
  --tap-target https://chatgpt.com/backend-api/codex --tap-no-launch --tap-port 0
# Verify log shows correct upstream URL
```

### Real E2E (optional, when auth is available)

```bash
# tmux-based real verification
tmux new-session -d -s verify \
  "uv run python -m claude_tap --tap-client codex --tap-target TARGET --tap-no-launch --tap-port 8080"
# In another window:
OPENAI_BASE_URL=http://127.0.0.1:8080/v1 codex exec "Reply: OK"
```

## Adding New Clients or Backends

When adding a new client or backend:

1. Add a row to the matrix above
2. Add a URL construction test case in `test_codex_upstream_url_construction`
3. Add an e2e test with fake upstream if applicable
4. Verify with real E2E if auth is available
5. Update README.md and README_zh.md with usage examples
