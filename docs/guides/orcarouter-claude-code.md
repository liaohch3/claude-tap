# Claude Code with OrcaRouter

This guide shows how to run Claude Code through [OrcaRouter](https://www.orcarouter.ai)'s Anthropic-compatible API while capturing the traffic with `claude-tap`.

OrcaRouter is an OpenAI- and Anthropic-compatible AI gateway. It exposes a provider/model namespace across many models through a single endpoint, with adaptive routing, automatic failover, and zero-markup inference. When you capture the session with `claude-tap`, keep the same Claude Code environment: `claude-tap` reads the OrcaRouter upstream from `ANTHROPIC_BASE_URL`, then launches Claude Code against the local proxy.

Simplified Chinese version: [Claude Code 搭配 OrcaRouter](orcarouter-claude-code.zh.md).

## Environment

Use `ANTHROPIC_AUTH_TOKEN` for Claude Code and leave `ANTHROPIC_API_KEY` unset to avoid Claude Code's API-key conflict prompt.

```bash
export ANTHROPIC_AUTH_TOKEN="<your OrcaRouter API key>"
unset ANTHROPIC_API_KEY

export ANTHROPIC_MODEL="orcarouter/auto"
export ANTHROPIC_DEFAULT_OPUS_MODEL="orcarouter/auto"
export ANTHROPIC_DEFAULT_SONNET_MODEL="orcarouter/auto"
export ANTHROPIC_DEFAULT_HAIKU_MODEL="orcarouter/fusion-flash"
export CLAUDE_CODE_SUBAGENT_MODEL="orcarouter/fusion-flash"
export ANTHROPIC_BASE_URL=https://api.orcarouter.ai
```

OrcaRouter model ids are namespaced, e.g. `orcarouter/auto`, `orcarouter/fusion`, `orcarouter/fusion-flash`, and `orcarouter/fusion-mini`. Use `orcarouter/auto` for adaptive routing across models, or a concrete id for a fixed model.

`claude-tap` uses the current `ANTHROPIC_BASE_URL` as the real upstream target before it overwrites the launched Claude Code process with the local proxy URL. Use `--tap-target` only when you want to override that detected upstream.

## Capture With claude-tap

Run `claude-tap` normally:

```bash
claude-tap -- --permission-mode bypassPermissions
```

For a one-off non-interactive smoke test:

```bash
claude-tap \
  -- \
  --permission-mode bypassPermissions \
  -p 'Use Bash to run pwd, then reply with ORCAROUTER_CLAUDE_TAP_OK.'
```

When the process exits, open the generated viewer:

```bash
open .traces/*/trace_*.html
```

## Pricing

The claude-tap viewer prices each traced request from its captured upstream host. OrcaRouter traffic (`https://api.orcarouter.ai`) is recognized and priced at the concrete underlying vendor model's rate — OrcaRouter routes such as `orcarouter/auto` resolve to the model the response reports, so a capture is billed at the actual vendor rate, not a gateway markup.

## Compatibility Notes

OrcaRouter's Anthropic-compatible endpoint accepts the `metadata.user_id` values Claude Code sends by default, so no request normalization is required. Common auth headers such as `Authorization` and `x-api-key` are redacted before recording, but prompts and tool output are stored as-is — do not put secrets in prompts or file content the agent may read.

## Verified Run

Validated on 2026-08-29 with:

- Direct OrcaRouter Anthropic API call (`POST /v1/messages`) returning HTTP `200`
- Direct OrcaRouter OpenAI-compatible call (`POST /v1/chat/completions`) returning HTTP `200`
- `ANTHROPIC_BASE_URL=https://api.orcarouter.ai`
- `orcarouter/auto` for main Claude Code turns
- Pricing namespace unit-tested for the `api.orcarouter.ai` host

## References

- [OrcaRouter](https://www.orcarouter.ai)
