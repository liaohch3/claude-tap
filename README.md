# claude-tap

[![PyPI version](https://img.shields.io/pypi/v/claude-tap.svg)](https://pypi.org/project/claude-tap/)
[![PyPI downloads](https://img.shields.io/pypi/dm/claude-tap.svg)](https://pypi.org/project/claude-tap/)
[![Python version](https://img.shields.io/pypi/pyversions/claude-tap.svg)](https://pypi.org/project/claude-tap/)
[![License](https://img.shields.io/github/license/liaohch3/claude-tap.svg)](https://github.com/liaohch3/claude-tap/blob/main/LICENSE)

[中文文档](README_zh.md)

Intercept and inspect all API traffic from [Claude Code](https://docs.anthropic.com/en/docs/claude-code). See exactly how it constructs system prompts, manages conversation history, selects tools, and uses tokens — in a beautiful trace viewer.

![Demo](docs/demo.gif)

![Light Mode](docs/viewer-light.png)

<details>
<summary>Dark Mode / Diff View</summary>

![Dark Mode](docs/viewer-dark.png)
![Structural Diff](docs/diff-modal.png)
![Character-level Diff](docs/billing-header-diff.png)

</details>

## Install

Requires Python 3.11+ and [Claude Code](https://docs.anthropic.com/en/docs/claude-code).

```bash
# Recommended
uv tool install claude-tap

# Or with pip
pip install claude-tap
```

Upgrade: `uv tool upgrade claude-tap` or `pip install --upgrade claude-tap`

## Usage

```bash
# Basic — launch Claude Code with tracing
claude-tap

# Live mode — watch API calls in real-time in browser
claude-tap --tap-live

# Pass any flags through to Claude Code
claude-tap -- --model claude-opus-4-6
claude-tap -c    # continue last conversation
```

When Claude Code exits, open the generated HTML viewer:

```bash
open .traces/trace_*.html
```

### CLI Options

All flags are forwarded to Claude Code, except these `--tap-*` ones:

```
--tap-live             Start real-time viewer (auto-opens browser)
--tap-live-port PORT   Port for live viewer server (default: auto)
--tap-open             Open HTML viewer in browser after exit
--tap-output-dir DIR   Trace output directory (default: ./.traces)
--tap-port PORT        Proxy port (default: auto)
--tap-target URL       Upstream API URL (default: https://api.anthropic.com)
--tap-no-launch        Only start the proxy, don't launch Claude Code
```

**Proxy-only mode** (useful for custom setups):

```bash
claude-tap --tap-no-launch --tap-port 8080
# In another terminal:
ANTHROPIC_BASE_URL=http://127.0.0.1:8080 claude
```

## Viewer Features

The viewer is a single self-contained HTML file (zero external dependencies):

- **Structural diff** — compare consecutive requests to see exactly what changed: new/removed messages, system prompt diffs, character-level inline highlighting
- **Path filtering** — filter by API endpoint (e.g., `/v1/messages` only)
- **Model grouping** — sidebar groups requests by model (Opus > Sonnet > Haiku)
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

1. `claude-tap` starts a reverse proxy and spawns Claude Code with `ANTHROPIC_BASE_URL` pointing to it
2. All API requests flow through the proxy → Anthropic API → back through proxy
3. SSE streaming responses are forwarded in real-time (zero added latency)
4. Each request-response pair is recorded to `trace.jsonl`
5. On exit, a self-contained HTML viewer is generated
6. Live mode (optional) broadcasts updates to browser via SSE

**Key features:** 🔒 API keys auto-redacted · ⚡ Zero latency · 📦 Self-contained viewer · 🔄 Real-time live mode

## License

MIT
