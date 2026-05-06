# Claude Code with DeepSeek API

This guide shows how to run Claude Code through DeepSeek's Anthropic-compatible API while capturing the traffic with `claude-tap`.

DeepSeek's official Claude Code guide points Claude Code at `https://api.deepseek.com/anthropic` and uses `deepseek-v4-pro[1m]` for the main Claude Code model. `claude-tap` should run in reverse proxy mode with the same DeepSeek Anthropic target.

## Environment

Use `ANTHROPIC_AUTH_TOKEN` for Claude Code and leave `ANTHROPIC_API_KEY` unset to avoid Claude Code's API-key conflict prompt.

```bash
export ANTHROPIC_AUTH_TOKEN="<your DeepSeek API key>"
unset ANTHROPIC_API_KEY

export ANTHROPIC_MODEL="deepseek-v4-pro[1m]"
export ANTHROPIC_DEFAULT_OPUS_MODEL="deepseek-v4-pro[1m]"
export ANTHROPIC_DEFAULT_SONNET_MODEL="deepseek-v4-pro[1m]"
export ANTHROPIC_DEFAULT_HAIKU_MODEL="deepseek-v4-flash"
export CLAUDE_CODE_SUBAGENT_MODEL="deepseek-v4-flash"
export CLAUDE_CODE_EFFORT_LEVEL=max
```

## Capture With claude-tap

Run `claude-tap` with an explicit DeepSeek Anthropic upstream:

```bash
claude-tap \
  --tap-proxy-mode reverse \
  --tap-target https://api.deepseek.com/anthropic \
  -- --permission-mode bypassPermissions
```

For a one-off non-interactive smoke test:

```bash
claude-tap \
  --tap-proxy-mode reverse \
  --tap-target https://api.deepseek.com/anthropic \
  -- \
  --permission-mode bypassPermissions \
  -p 'Use Bash to run pwd, then reply with DEEPSEEK_CLAUDE_TAP_OK.'
```

When the process exits, open the generated viewer:

```bash
open .traces/*/trace_*.html
```

## TLS and Local Proxies

If the upstream request fails with `SSLCertVerificationError` while direct `curl` calls succeed, the Python process may be using a CA bundle that does not trust your local outbound proxy. On macOS/Homebrew Python, run `claude-tap` with the system bundle or the CA bundle used by your proxy:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem claude-tap \
  --tap-proxy-mode reverse \
  --tap-target https://api.deepseek.com/anthropic
```

## Compatibility Notes

Claude Code 2.1.128 sends `metadata.user_id` as a JSON string. DeepSeek's Anthropic-compatible endpoint rejects that value because it only accepts letters, digits, underscores, and hyphens. `claude-tap` normalizes invalid `metadata.user_id` values only when the upstream target is `https://api.deepseek.com/anthropic`; default Anthropic traffic is left unchanged.

DeepSeek currently returns `404` for Claude Code's `/v1/models?limit=1000` preflight. Claude Code continues as long as `/v1/messages` succeeds.

## Verified Run

Validated on 2026-05-06 with:

- Claude Code `2.1.128`
- `deepseek-v4-pro[1m]` for main Claude Code turns
- `deepseek-v4-flash` for Claude Code title/auxiliary turns
- `claude-tap --tap-proxy-mode reverse --tap-target https://api.deepseek.com/anthropic`

The interactive tmux run produced:

- 8 `/v1/messages` requests
- 4 `Bash` `tool_use` blocks
- 14 `tool_result` blocks
- A generated HTML viewer from the real trace under `.traces/`

Tool-use response:

![DeepSeek Claude Code tool use](../images/deepseek-claude-code-tool-use.png)

Final multi-turn response:

![DeepSeek Claude Code final response](../images/deepseek-claude-code-final-response.png)

## References

- [DeepSeek Anthropic API](https://api-docs.deepseek.com/guides/anthropic_api)
- [DeepSeek Claude Code integration](https://api-docs.deepseek.com/quick_start/agent_integrations/claude_code)
