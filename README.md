# claude-tap

[![PyPI version](https://img.shields.io/pypi/v/claude-tap.svg)](https://pypi.org/project/claude-tap/)
[![PyPI downloads](https://img.shields.io/pypi/dm/claude-tap.svg)](https://pypi.org/project/claude-tap/)
[![Python version](https://img.shields.io/pypi/pyversions/claude-tap.svg)](https://pypi.org/project/claude-tap/)
[![License](https://img.shields.io/github/license/liaohch3/claude-tap.svg)](https://github.com/liaohch3/claude-tap/blob/main/LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/liaohch3/claude-tap?style=social)](https://github.com/liaohch3/claude-tap/stargazers)
[![All Contributors](https://img.shields.io/badge/all_contributors-6-orange.svg)](#contributors)

[中文文档](README_zh.md)

Intercept and inspect API traffic from [Claude Code](https://docs.anthropic.com/en/docs/claude-code), [Codex CLI](https://github.com/openai/codex), OpenCode, or [Cursor CLI](https://cursor.com/cli). See exactly how they construct system prompts, manage conversation history, select tools, and use tokens — in a beautiful trace viewer.

![Demo](docs/demo.gif)

![Light Mode](docs/viewer-light.png)

<details>
<summary>Dark Mode / Diff View</summary>

![Dark Mode](docs/viewer-dark.png)
![Structural Diff](docs/diff-modal.png)
![Character-level Diff](docs/billing-header-diff.png)

</details>

> **OpenClaw:** If you are Openclaw and integrating claude-tap with [OpenClaw](docs/guides/OPENCLAW_README.md), also read the dedicated setup guide.

## Install

Requires Python 3.11+ and the CLI you want to trace: [Claude Code](https://docs.anthropic.com/en/docs/claude-code), [Codex CLI](https://github.com/openai/codex), OpenCode, or [Cursor CLI](https://cursor.com/cli).

```bash
# Recommended
uv tool install claude-tap

# Or with pip
pip install claude-tap
```

Upgrade: `uv tool upgrade claude-tap` or `pip install --upgrade claude-tap`

## Usage

### Claude Code

```bash
# Basic — launch Claude Code with tracing
claude-tap

# Live mode — watch API calls in real-time in browser
claude-tap --tap-live

# Pass any flags through to Claude Code
claude-tap -- --model claude-opus-4-6
claude-tap -c    # continue last conversation

# Skip all permission prompts (auto-accept tool calls)
claude-tap -- --dangerously-skip-permissions

# Full-power combo: live viewer + skip permissions + specific model
claude-tap --tap-live -- --dangerously-skip-permissions --model claude-sonnet-4-6
```

### Codex CLI

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

# OAuth + full auto + live viewer
claude-tap --tap-client codex --tap-live -- --full-auto
```

### Cursor CLI

Cursor CLI uses forward proxy mode by default. Use `--model auto` on free plans, and omit `--mode ask` when you want tool calls.

```bash
claude-tap --tap-client cursor -- -p --trust --model auto "hello"
claude-tap --tap-client cursor -- -p --trust --model auto --continue "continue"
```

### Browser Preview

```bash
# Disable auto-open of HTML viewer after exit (on by default)
claude-tap --tap-no-open

# Live mode — real-time viewer opens in browser while client runs
claude-tap --tap-live
claude-tap --tap-live --tap-live-port 3000    # fixed port for live viewer

# Standalone dashboard — browse trace history without launching a client
claude-tap dashboard
claude-tap dashboard --tap-output-dir ./my-traces --tap-live-port 3000
```

When the client exits, you can also manually open the generated viewer:

```bash
open .traces/*/trace_*.html
```

You can also regenerate a self-contained HTML viewer from an existing JSONL trace:

```bash
claude-tap export .traces/2026-02-28/trace_141557.jsonl -o trace.html
# or:
claude-tap export .traces/2026-02-28/trace_141557.jsonl --format html
```

### Proxy-Only Mode

Start the proxy without launching a client — useful for custom setups or connecting from a separate terminal:

```bash
# Claude Code
claude-tap --tap-no-launch --tap-port 8080
# In another terminal:
ANTHROPIC_BASE_URL=http://127.0.0.1:8080 claude

# Codex CLI (OAuth)
claude-tap --tap-client codex --tap-target https://chatgpt.com/backend-api/codex --tap-no-launch --tap-port 8080
# In another terminal:
OPENAI_BASE_URL=http://127.0.0.1:8080/v1 codex -c 'openai_base_url="http://127.0.0.1:8080/v1"'

# Codex CLI (API Key)
claude-tap --tap-client codex --tap-no-launch --tap-port 8080
# In another terminal:
OPENAI_BASE_URL=http://127.0.0.1:8080/v1 codex -c 'openai_base_url="http://127.0.0.1:8080/v1"'
```

Run `claude-tap --help` for all options. Flags that are not `--tap-*` are forwarded to the selected client.

## Viewer Features

The viewer is a single self-contained HTML file (zero external dependencies):

- **Structural diff** — compare consecutive requests to see exactly what changed: new/removed messages, system prompt diffs, character-level inline highlighting
- **Path filtering** — filter by API endpoint (e.g., `/v1/messages` only)
- **Model grouping** — sidebar groups requests by model, with Claude-family priority ordering
- **Token usage breakdown** — input / output / cache read / cache creation
- **Tool inspector** — expandable cards with tool name, description, and parameter schema
- **Search** — full-text search across messages, tools, prompts, and responses
- **Dark mode** — toggle light/dark themes (respects system preference)
- **Keyboard navigation** — `j`/`k` or arrow keys
- **Copy helpers** — one-click copy of request JSON or cURL command
- **i18n** — English, 简体中文, 日本語, 한국어, Français, العربية, Deutsch, Русский

## Architecture

![Architecture](docs/architecture.png)

**How it works:**

1. `claude-tap` starts a reverse or forward proxy and spawns the selected client
2. Base URL clients are pointed at the reverse proxy; clients without base URL support use proxy/CA environment variables
3. SSE and WebSocket streams are forwarded as chunks/messages arrive with low proxy overhead
4. Each request-response pair or WebSocket session is recorded to a dated `trace_*.jsonl`
5. On exit, a self-contained HTML viewer is generated
6. Live mode (optional) broadcasts updates to browser via SSE

**Key features:** 🔒 Common auth headers auto-redacted · ⚡ Low-overhead streaming · 📦 Self-contained viewer · 🔄 Real-time live mode

## Community

### Star History

[![Star History Chart](https://api.star-history.com/svg?repos=liaohch3/claude-tap&type=Date)](https://www.star-history.com/#liaohch3/claude-tap&Date)

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
