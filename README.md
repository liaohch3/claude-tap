# claude-tap

[![PyPI version](https://img.shields.io/pypi/v/claude-tap.svg)](https://pypi.org/project/claude-tap/)
[![PyPI downloads](https://img.shields.io/pypi/dm/claude-tap.svg)](https://pypi.org/project/claude-tap/)
[![Python version](https://img.shields.io/pypi/pyversions/claude-tap.svg)](https://pypi.org/project/claude-tap/)
[![License](https://img.shields.io/github/license/liaohch3/claude-tap.svg)](https://github.com/liaohch3/claude-tap/blob/main/LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/liaohch3/claude-tap?style=social)](https://github.com/liaohch3/claude-tap/stargazers)
[![All Contributors](https://img.shields.io/badge/all_contributors-9-orange.svg)](#contributors)

[中文文档](README_zh.md)

`claude-tap` is a local proxy and trace viewer for AI coding agents. Run your CLI through it, then inspect the real API traffic: system prompts, conversation history, tool schemas, tool calls, streaming responses, token usage, and request diffs.

It works with [Claude Code](https://docs.anthropic.com/en/docs/claude-code), [Codex CLI](https://github.com/openai/codex), [Gemini CLI](https://github.com/google-gemini/gemini-cli), [Kimi CLI](https://github.com/MoonshotAI/kimi-cli), [OpenCode](https://opencode.ai), [Pi](https://github.com/badlogic/pi-mono/tree/main/packages/coding-agent), [Hermes Agent](https://github.com/NousResearch/hermes-agent), [Cursor CLI](https://cursor.com/cli), [Qoder CLI](https://qoder.com/cli), [Antigravity CLI](https://antigravity.google/product/antigravity-cli), and [CodeBuddy CLI](https://www.codebuddy.ai).

<p align="center">
  <img src="docs/demo.gif" alt="claude-tap demo showing a real Codex trace" width="100%">
  <br>
  <sub>Open a real agent run, inspect every request, and compare how context changes between turns.</sub>
</p>

<table>
  <tr>
    <td width="33%" align="center">
      <img src="docs/viewer-light.png" alt="Light mode trace viewer" width="100%">
      <br>
      <sub>Light viewer overview</sub>
    </td>
    <td width="33%" align="center">
      <img src="docs/viewer-dark.png" alt="Dark mode trace viewer" width="100%">
      <br>
      <sub>Dark mode for long review sessions</sub>
    </td>
    <td width="33%" align="center">
      <img src="docs/diff-modal.png" alt="Structured diff modal" width="100%">
      <br>
      <sub>Structured diff across adjacent requests</sub>
    </td>
  </tr>
</table>

## Why use it

- 👀 **See the exact context**: inspect prompts, messages, tool definitions, tool calls, tool results, reconstructed streaming responses, and token usage.
- 🔎 **Debug behavior with evidence**: compare adjacent requests and pinpoint which prompt, message, tool, or parameter changed.
- 📦 **Share one portable artifact**: each run writes a local trace session that can be exported to a self-contained HTML viewer for review or archiving.
- 🔒 **Keep traces on your machine**: no hosted dashboard is required, and common auth headers are redacted before recording.
- 🧩 **Use one workflow across clients**: trace Claude Code, Codex CLI, Gemini CLI, Kimi CLI, OpenCode, Pi, Hermes Agent, Cursor CLI, Qoder CLI, and CodeBuddy.

## Supported Clients

| Client | Typical use |
|--------|-------------|
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | Anthropic API or Claude-compatible gateways such as DeepSeek / GLM |
| [Codex CLI](https://github.com/openai/codex) | OpenAI API key mode or ChatGPT subscription OAuth |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli) | Google OAuth / Code Assist traffic |
| [Kimi CLI](https://github.com/MoonshotAI/kimi-cli) | Kimi Code or Moonshot Open Platform |
| [OpenCode](https://opencode.ai) | Multi-provider OpenCode sessions |
| [Pi](https://github.com/badlogic/pi-mono/tree/main/packages/coding-agent) | Pi sessions, including OpenAI Codex OAuth providers |
| [Hermes Agent](https://github.com/NousResearch/hermes-agent) | Multi-provider Hermes TUI or gateway sessions |
| [Cursor CLI](https://cursor.com/cli) | Cursor Agent sessions plus readable local transcript import |
| [Qoder CLI](https://qoder.com/cli) | Qoder Agent sessions through forward proxy mode |
| [Antigravity CLI](https://antigravity.google/product/antigravity-cli) | Antigravity Agent sessions through forward proxy mode |
| [CodeBuddy CLI](https://www.codebuddy.ai) | Tencent CodeBuddy SaaS or internal Copilot endpoint |

## Install

Requires Python 3.11+ and the client you want to trace.

```bash
# Recommended
uv tool install claude-tap

# Or with pip
pip install claude-tap
```

Upgrade: `claude-tap update`, `uv tool upgrade claude-tap`, or `pip install --upgrade claude-tap`

## Quick Start

Run the client you want to inspect through `claude-tap`. Flags after `--` are passed to the selected client.

```bash
# Claude Code with the live browser viewer enabled by default
claude-tap

# Restore pre-v0.1.75 behavior: no live viewer server
claude-tap --tap-no-live

# Codex CLI
claude-tap --tap-client codex

# Gemini CLI
claude-tap --tap-client gemini -- -p "hello"

# Kimi CLI
claude-tap --tap-client kimi

# Pi
claude-tap --tap-client pi -- --model openai-codex/gpt-5.3-codex-spark -p "hello"

# Cursor CLI
claude-tap --tap-client cursor -- -p --trust --model auto "hello"

# Qoder CLI
claude-tap --tap-client qoder -- -p "hello" --permission-mode dont_ask

# Antigravity CLI
claude-tap --tap-client agy

# CodeBuddy CLI
claude-tap --tap-client codebuddy
```

<details>
<summary>Claude Code examples</summary>

```bash
# Pass flags through to Claude Code
claude-tap -- --model claude-opus-4-6
claude-tap -c    # continue last conversation

# Skip all permission prompts (auto-accept tool calls)
claude-tap -- --dangerously-skip-permissions

# Live viewer is on by default; pass Claude flags after --
claude-tap -- --dangerously-skip-permissions --model claude-sonnet-4-6
```

`claude-tap` auto-detects custom Claude Code upstreams from `ANTHROPIC_BASE_URL`
in your environment or Claude settings. Use `--tap-target` only when you want to
override that detected target.

For the Claude Code VS Code extension, set `Claude Code: Claude Process Wrapper` to `claude-tap`; on Windows, use the full `claude-tap.exe` path if VS Code cannot find it.

</details>

<details>
<summary>Claude Code with DeepSeek API</summary>

Full English guide: [Claude Code with DeepSeek API](docs/guides/deepseek-claude-code.md). Simplified Chinese version: [Claude Code 搭配 DeepSeek API](docs/guides/deepseek-claude-code.zh.md).

```bash
export ANTHROPIC_AUTH_TOKEN="<your DeepSeek API key>"
unset ANTHROPIC_API_KEY

export ANTHROPIC_MODEL="deepseek-v4-pro[1m]"
export ANTHROPIC_DEFAULT_OPUS_MODEL="deepseek-v4-pro[1m]"
export ANTHROPIC_DEFAULT_SONNET_MODEL="deepseek-v4-pro[1m]"
export ANTHROPIC_DEFAULT_HAIKU_MODEL="deepseek-v4-flash"
export CLAUDE_CODE_SUBAGENT_MODEL="deepseek-v4-flash"
export CLAUDE_CODE_EFFORT_LEVEL=max
export ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
```

```bash
claude-tap -- --permission-mode bypassPermissions
```

`claude-tap` reads the DeepSeek upstream from `ANTHROPIC_BASE_URL`, then launches Claude Code against the local proxy. Use `--tap-target https://api.deepseek.com/anthropic` only as a manual override.

</details>

<details>
<summary>Codex CLI auth modes and examples</summary>

Codex CLI supports two authentication modes with different upstream targets:

| Auth Mode | How to authenticate | Upstream target | Notes |
|-----------|-------------------|-----------------|-------|
| **OAuth** (ChatGPT subscription) | `codex login` | `https://chatgpt.com/backend-api/codex` | Default for ChatGPT Plus/Pro/Team users |
| **API Key** | Set `OPENAI_API_KEY` | `https://api.openai.com` (default) | Pay-per-use via OpenAI Platform |

`claude-tap` auto-detects the Codex target from your auth state when possible.

```bash
# OAuth users (ChatGPT Plus/Pro/Team) — auto-detected after `codex login`
claude-tap --tap-client codex

# If auto-detection cannot read your Codex auth file, specify the target explicitly
claude-tap --tap-client codex --tap-target https://chatgpt.com/backend-api/codex

# API Key users — default OpenAI API target works out of the box
claude-tap --tap-client codex

# With specific model
claude-tap --tap-client codex -- --model codex-mini-latest

# Full auto-approval (skip all permission prompts)
claude-tap --tap-client codex -- --full-auto

# OAuth + full auto; live viewer is enabled by default
claude-tap --tap-client codex -- --full-auto
```

</details>

<details>
<summary>Kimi CLI examples</summary>

Kimi CLI uses reverse proxy mode by default through `KIMI_BASE_URL`. Use your existing Kimi CLI auth/config; the default upstream target is the Kimi Code API.

```bash
claude-tap --tap-client kimi
claude-tap --tap-client kimi -- --thinking

# Use Moonshot Open Platform instead of Kimi Code
claude-tap --tap-client kimi --tap-target https://api.moonshot.ai/v1
```

</details>

<details>
<summary>Gemini CLI examples</summary>

Gemini CLI uses forward proxy mode by default. Google OAuth / Code Assist traffic goes to several Google endpoints, so forward proxy capture is the safest default. Reverse mode remains available for API-key or Vertex-style flows that honor `GOOGLE_GEMINI_BASE_URL` or `GOOGLE_VERTEX_BASE_URL`.

```bash
# Google OAuth / Code Assist
claude-tap --tap-client gemini -- -p "hello"

# Live viewer is enabled by default
claude-tap --tap-client gemini -- -p "hello"

# Reverse mode for compatible API-key / Vertex flows
claude-tap --tap-client gemini --tap-proxy-mode reverse -- -p "hello"
```

</details>

<details>
<summary>OpenCode examples</summary>

[OpenCode](https://opencode.ai) is a multi-provider terminal AI assistant. Because it can talk to many providers, claude-tap defaults to **forward proxy** mode for opencode: it injects `HTTPS_PROXY` plus the local CA into the child process so traffic to any provider is captured.

```bash
# Forward proxy mode — captures every provider opencode talks to (default)
claude-tap --tap-client opencode

# Live viewer is enabled by default
claude-tap --tap-client opencode

# Reverse mode — only works when using Anthropic provider (single ANTHROPIC_BASE_URL)
claude-tap --tap-client opencode --tap-proxy-mode reverse
```

</details>

<details>
<summary>Pi examples</summary>

[Pi](https://github.com/badlogic/pi-mono/tree/main/packages/coding-agent) is a multi-provider coding agent. claude-tap defaults to **forward proxy** mode for Pi because Pi can use subscription OAuth providers such as `openai-codex` and custom API-key providers from its model registry.

```bash
# OpenAI Codex OAuth via Pi's openai-codex provider
claude-tap --tap-client pi -- --model openai-codex/gpt-5.3-codex-spark -p "hello"

# Live viewer is enabled by default
claude-tap --tap-client pi -- --model openai-codex/gpt-5.3-codex-spark -p "hello"

# Read-only tool capture
claude-tap --tap-client pi -- --model openai-codex/gpt-5.3-codex-spark --tools bash -p "Run pwd"
```

Pi stores OAuth credentials in `~/.pi/agent/auth.json` after `/login`. If you keep Pi credentials in another directory, set `PI_CODING_AGENT_DIR` before launching `claude-tap`.

</details>

<details>
<summary>Hermes Agent examples</summary>

Hermes Agent is a multi-provider Python AI agent (Nous Portal, OpenRouter, NVIDIA NIM, Xiaomi MiMo, GLM, Kimi, MiniMax, Hugging Face, OpenAI, Anthropic, custom). Because it can talk to any of these providers — and `httpx` / `requests` both honor `HTTPS_PROXY` natively — claude-tap defaults to **forward proxy** mode for hermes: it injects `HTTPS_PROXY` plus the local CA into the child process so any provider is captured.

```bash
# Interactive TUI — the recommended way for local trace capture.
claude-tap --tap-client hermes

# Gateway mode — captures LLM calls triggered by incoming platform messages (Slack, Telegram, etc.).
# Requires a messaging platform configured in ~/.hermes/.env.
# claude-tap auto-rewrites `gateway start` → `gateway run` so the gateway runs in the
# foreground and inherits HTTPS_PROXY; without this, the daemon spawned by systemd/launchd
# would not go through the proxy and no traces would be recorded.
claude-tap --tap-client hermes -- gateway start

# Reverse mode is opt-in and only useful when ~/.hermes is configured with an
# OpenAI-compatible provider that reads OPENAI_BASE_URL.
claude-tap --tap-client hermes --tap-proxy-mode reverse
```

> **Note:** Gateway mode only produces traces when a configured messaging platform (Slack, Telegram, etc.) delivers a message to the bot. Without an active platform integration, the gateway makes no LLM calls and no traces are recorded.

</details>

<details>
<summary>Cursor CLI examples</summary>

Cursor CLI uses forward proxy mode by default. Use `--model auto` on free plans, and omit `--mode ask` when you want tool calls.

```bash
claude-tap --tap-client cursor -- -p --trust --model auto "hello"
claude-tap --tap-client cursor -- -p --trust --model auto --continue "continue"
```

</details>

## Guides and Integrations

- [OpenClaw setup guide](docs/guides/OPENCLAW_README.md) for integrating `claude-tap` with OpenClaw. Simplified Chinese version: [OpenClaw 设置指南](docs/guides/OPENCLAW_README.zh.md).
- [Claude Code with DeepSeek API](docs/guides/deepseek-claude-code.md) for routing Claude Code through DeepSeek's Anthropic-compatible API. Simplified Chinese version: [Claude Code 搭配 DeepSeek API](docs/guides/deepseek-claude-code.zh.md).
- [Client support matrix](docs/support-matrix.md) for exact environment variables, proxy modes, and URL rewrite rules.

<details>
<summary>Qoder CLI examples</summary>

Qoder CLI talks to multiple Qoder endpoints, so claude-tap defaults to **forward proxy** mode for `--tap-client qoder`.

```bash
# Browser login, PAT, or job token must be configured before launch.
qodercli login

claude-tap --tap-client qoder -- -p "hello" --permission-mode dont_ask
```

</details>

<details>
<summary>Antigravity CLI examples</summary>

Antigravity CLI talks to multiple Google/Antigravity endpoints, so claude-tap defaults to **forward proxy** mode for `--tap-client agy`. Its Code Assist model API also honors `CLOUD_CODE_URL`; claude-tap injects that automatically so model requests such as `/v1internal:streamGenerateContent` are captured by the same local proxy.

On macOS, Antigravity may not honor per-process CA environment variables. claude-tap automatically trusts the local CA in your current user's login keychain on first `agy` launch. This does not use `sudo` or the System keychain, though macOS may prompt to unlock the login keychain.

```bash
claude-tap --tap-client agy --tap-live

# Optional: trust the CA separately before launching a forward-proxy client.
claude-tap trust-ca
```

</details>

<details>
<summary>CodeBuddy CLI examples</summary>

CodeBuddy uses reverse proxy mode by default. claude-tap auto-detects the upstream from CodeBuddy's own login cache (`~/.codebuddy/local_storage/`), so iOA / WeChat / Google-Github / Enterprise-Domain login modes all work without any extra flag. When the cache is missing (e.g. before first login), it falls back to `https://copilot.tencent.com/v2`.

```bash
# Auto-detected endpoint (works for all four login modes once logged in)
claude-tap --tap-client codebuddy

# Explicit override (e.g. external SaaS or staging)
claude-tap --tap-client codebuddy --tap-target https://www.codebuddy.ai/v2

# Or via environment variable
CODEBUDDY_BASE_URL=https://www.codebuddy.ai/v2 claude-tap --tap-client codebuddy -- -p "Reply OK"
```

</details>

<details>
<summary>Viewer, export, and advanced options</summary>

```bash
# Live viewer runs by default while a client runs
claude-tap

# Disable live viewer for scripts, CI, remote shells, or old behavior
claude-tap --tap-no-live

# Browse saved traces without launching a client
claude-tap dashboard

# Regenerate a self-contained HTML viewer from JSONL
claude-tap export .traces/2026-02-28/trace_141557.jsonl -o trace.html

# Embed the exported viewer in an iframe with reduced chrome
# trace.html?embed=1&hideHeader=1&hidePath=1&hideHistory=1&hideControls=1&density=compact&theme=light

# Store traces in another directory, or keep fewer sessions
claude-tap --tap-output-dir ./my-traces
claude-tap --tap-max-traces 10

# Start only the proxy for custom setups
claude-tap --tap-no-launch --tap-port 8080

# Disable browser auto-open for live and generated viewers
claude-tap --tap-no-open
```

In proxy-only mode, start your client in another terminal and point its base URL or proxy settings at the local proxy. Use the [client support matrix](docs/support-matrix.md) for exact wiring.

When used as VSCode Claude Code's `claudeProcessWrapper`, claude-tap honors the Claude binary path passed by the extension.

### CLI Options

All flags are forwarded to the selected client, except these `--tap-*` ones:

```
--tap-client CLIENT      Client to launch: claude (default), agy, codex, gemini, kimi, opencode, pi, hermes, cursor, qoder, or codebuddy
--tap-target URL         Upstream API URL (default: auto per client)
--tap-live               Start real-time viewer while the client runs (default: on)
--tap-no-live            Disable the real-time viewer server (pre-v0.1.75 behavior)
--tap-live-port PORT     Port for live viewer server (default: auto)
--tap-no-open            Don't auto-open live or generated HTML viewers in a browser
--tap-output-dir DIR     Trace output directory (default: ./.traces)
--tap-port PORT          Proxy port (default: auto)
--tap-host HOST          Bind address (default: 127.0.0.1, or 0.0.0.0 in --tap-no-launch mode)
--tap-no-launch          Only start the proxy, don't launch client
--tap-max-traces N       Max trace sessions to keep (default: 50, 0 = unlimited)
--tap-store-stream-events Persist raw SSE/WebSocket event arrays during capture so viewer/export output can show them (default: off)
--tap-no-update-check    Disable PyPI update check on startup
--tap-no-auto-update     Check for updates but don't auto-download
--tap-proxy-mode MODE    Proxy mode: reverse or forward (default: reverse for claude/codex/kimi/codebuddy, forward for agy/gemini/opencode/pi/hermes/cursor/qoder)
--tap-trust-ca           On macOS, explicitly trust the local CA in the user login keychain before launch (agy does this automatically)
```

</details>

## Viewer Features

### Trace viewer capabilities

The viewer is a single self-contained HTML file (zero external dependencies):

- **Structural diff** — compare consecutive requests to see exactly what changed: new/removed messages, system prompt diffs, character-level inline highlighting
- **Path filtering** — filter by API endpoint (e.g., `/v1/messages` only)
- **Model grouping** — sidebar groups requests by model, with Claude-family priority ordering
- **Token usage breakdown** — input / output / cache read / cache creation
- **Tool inspector** — expandable cards with tool name, description, and parameter schema
- **Search** — full-text search across messages, tools, prompts, and responses
- **Dark mode** — toggle light/dark themes (respects system preference)
- **Iframe embed mode** — add query parameters such as `embed=1`, `hideHeader=1`, `hidePath=1`, `hideHistory=1`, `hideControls=1`, `density=compact`, and `theme=light|dark`
- **Keyboard navigation** — `j`/`k` or arrow keys
- **Copy helpers** — one-click copy of request JSON or cURL command
- **i18n** — English, 简体中文, 日本語, 한국어, Français, العربية, Deutsch, Русский

## Architecture

![Architecture](docs/architecture.png)

<details>
<summary>How it works</summary>

**How it works:**

1. `claude-tap` starts a reverse or forward proxy and spawns the selected client
2. Base URL clients are pointed at the reverse proxy; clients without base URL support use proxy/CA environment variables
3. SSE and WebSocket streams are forwarded as chunks/messages arrive with low proxy overhead
4. Each request-response pair or WebSocket session is recorded to local trace storage; raw SSE/WebSocket event arrays are omitted by default and must be captured with `--tap-store-stream-events` if you need them later in viewer/export output
5. On exit, a self-contained HTML viewer is generated
6. Live mode is enabled by default and broadcasts updates to the browser via SSE

**Key features:** 🔒 Common auth headers auto-redacted · ⚡ Low-overhead streaming · 📦 Self-contained viewer · 🔄 Real-time live mode

</details>

## Community

### Star History

<a href="https://www.star-history.com/?repos=liaohch3%2Fclaude-tap&type=date&legend=bottom-right">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=liaohch3/claude-tap&type=date&theme=dark&legend=top-left" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=liaohch3/claude-tap&type=date&legend=top-left" />
    <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=liaohch3/claude-tap&type=date&legend=top-left" />
  </picture>
</a>

### Contributors

Thanks goes to these contributors:

<!-- ALL-CONTRIBUTORS-LIST:START - Do not remove or modify this section -->
<!-- prettier-ignore-start -->
<!-- markdownlint-disable -->
<table>
  <tbody>
    <tr>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/liaohch3"><img src="https://avatars.githubusercontent.com/u/34056481?s=100" width="100px;" alt="liaohch3"/><br /><sub><b>liaohch3</b></sub></a><br /><a href="https://github.com/liaohch3/claude-tap/commits?author=liaohch3" title="Code">💻</a> <a href="https://github.com/liaohch3/claude-tap/commits?author=liaohch3" title="Documentation">📖</a> <a href="#maintenance-liaohch3" title="Maintenance">🚧</a> <a href="https://github.com/liaohch3/claude-tap/commits?author=liaohch3" title="Tests">⚠️</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/WEIFENG2333"><img src="https://avatars.githubusercontent.com/u/61730227?s=100" width="100px;" alt="BKK"/><br /><sub><b>BKK</b></sub></a><br /><a href="https://github.com/liaohch3/claude-tap/commits?author=WEIFENG2333" title="Code">💻</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/YoungCan-Wang"><img src="https://avatars.githubusercontent.com/u/73347006?s=100" width="100px;" alt="YoungCan-Wang"/><br /><sub><b>YoungCan-Wang</b></sub></a><br /><a href="https://github.com/liaohch3/claude-tap/commits?author=YoungCan-Wang" title="Code">💻</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/oxkrypton"><img src="https://avatars.githubusercontent.com/u/154910746?s=100" width="100px;" alt="0xkrypton"/><br /><sub><b>0xkrypton</b></sub></a><br /><a href="https://github.com/liaohch3/claude-tap/commits?author=oxkrypton" title="Code">💻</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/googs1025"><img src="https://avatars.githubusercontent.com/u/86391540?s=100" width="100px;" alt="CYJiang"/><br /><sub><b>CYJiang</b></sub></a><br /><a href="https://github.com/liaohch3/claude-tap/commits?author=googs1025" title="Code">💻</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/TITOCHAN2023"><img src="https://avatars.githubusercontent.com/u/138754853?s=100" width="100px;" alt="陈展鹏"/><br /><sub><b>陈展鹏</b></sub></a><br /><a href="https://github.com/liaohch3/claude-tap/commits?author=TITOCHAN2023" title="Documentation">📖</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/devtalker"><img src="https://avatars.githubusercontent.com/u/23204195?s=100" width="100px;" alt="devtalker"/><br /><sub><b>devtalker</b></sub></a><br /><a href="https://github.com/liaohch3/claude-tap/commits?author=devtalker" title="Code">💻</a></td>
    </tr>
    <tr>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/dingyaguang117"><img src="https://avatars.githubusercontent.com/u/1930778?s=100" width="100px;" alt="Yaguang Ding"/><br /><sub><b>Yaguang Ding</b></sub></a><br /><a href="https://github.com/liaohch3/claude-tap/commits?author=dingyaguang117" title="Code">💻</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/sephymartin"><img src="https://avatars.githubusercontent.com/u/299891?s=100" width="100px;" alt="Sephy"/><br /><sub><b>Sephy</b></sub></a><br /><a href="https://github.com/liaohch3/claude-tap/commits?author=sephymartin" title="Code">💻</a></td>
    </tr>
  </tbody>
</table>

<!-- markdownlint-restore -->
<!-- prettier-ignore-end -->

<!-- ALL-CONTRIBUTORS-LIST:END -->

## Contributing

Contributions are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT
