# claude-tap

A CLI tool that wraps [Claude Code](https://docs.anthropic.com/en/docs/claude-code) with a local reverse proxy to intercept and record all API requests. Useful for studying Claude Code's **Context Engineering** — how it constructs system prompts, manages conversation history, selects tools, and optimizes token usage across turns.

## How It Works

```
claude-tap
  ├─ Starts a local HTTP reverse proxy (127.0.0.1:PORT)
  ├─ Launches Claude Code with ANTHROPIC_BASE_URL=http://127.0.0.1:PORT
  ├─ Claude Code sends requests to the proxy (plain HTTP, no TLS needed)
  ├─ Proxy forwards requests to api.anthropic.com via HTTPS
  ├─ SSE streaming responses are forwarded in real-time (zero added latency)
  ├─ Each request-response pair is recorded to a JSONL trace file
  └─ On exit, generates a self-contained HTML viewer
```

## Quick Start

### Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (recommended) or pip
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI installed

### Install & Run

```bash
# Install from PyPI
pip install claude-tap

# Or install from source
git clone https://github.com/liaohch3/claude-tap.git
cd claude-tap
uv sync

# Run (launches Claude Code with tracing enabled)
claude-tap

# Or with arguments passed to Claude Code
claude-tap -- --model claude-opus-4-6

# Or run as a Python module
uv run python -m claude_tap
```

### View the Trace

After Claude Code exits, the tool outputs three files in `./.traces/`:

| File | Description |
|------|-------------|
| `trace_YYYYMMDD_HHMMSS.jsonl` | Raw trace data (one JSON record per API call) |
| `trace_YYYYMMDD_HHMMSS.log` | Proxy debug log |
| `trace_YYYYMMDD_HHMMSS.html` | Self-contained HTML viewer (open in any browser) |

```bash
open .traces/trace_*.html
```

## CLI Options

```
usage: claude-tap [OPTIONS] [-- CLAUDE_ARGS...]

Options:
  -o, --output-dir DIR   Trace output directory (default: ./.traces)
  -p, --port PORT        Proxy port (default: 0 = auto-assign)
  -t, --target URL       Upstream API URL (default: https://api.anthropic.com)
  --no-launch            Only start the proxy, don't launch Claude Code
```

**Proxy-only mode** (useful for custom setups):

```bash
claude-tap --no-launch -p 8080
# In another terminal:
ANTHROPIC_BASE_URL=http://127.0.0.1:8080 claude
```

## Screenshots

### Light Mode
![Light Mode](docs/viewer-light.png)

### Dark Mode
![Dark Mode](docs/viewer-dark.png)

### Structural Diff
Compare consecutive API requests to see exactly what changed — messages added/removed, system prompt diffs, parameter changes:

![Structural Diff](docs/diff-modal.png)

### i18n (Chinese)
![Chinese i18n](docs/viewer-zh.png)

## HTML Viewer Features

The viewer is a single self-contained HTML file (no external dependencies) with:

- **Path filtering** — filter by API endpoint (e.g., `/v1/messages` only)
- **Model grouping** — sidebar groups requests by model (Opus > Sonnet > Haiku)
- **Expandable sections** — System Prompt, Messages, Tools, Response, Token Usage, SSE Events, Full JSON
- **Tool inspector** — expandable cards showing tool name, description, and parameter schema
- **Token usage breakdown** — input / output / cache read / cache creation
- **Structural diff** — compare consecutive same-model requests to see what changed:
  - New/removed messages highlighted
  - System prompt text diff with line-by-line changes
  - Tools and parameter-level field diff
- **Search & filter** — full-text search across messages, tools, prompts, and responses
- **Dark mode** — toggle between light/dark themes (respects system preference)
- **Keyboard navigation** — `j`/`k` or arrow keys to navigate between turns
- **Copy helpers** — one-click copy of request JSON or cURL command
- **i18n** — supports 8 languages: English, Chinese (Simplified), Japanese, Korean, French, Arabic (RTL), German, Russian

## JSONL Record Format

Each line in the `.jsonl` file is a JSON object:

```json
{
  "timestamp": "2026-02-15T10:30:00.000Z",
  "request_id": "req_a1b2c3d4e5f6",
  "turn": 1,
  "duration_ms": 1234,
  "request": {
    "method": "POST",
    "path": "/v1/messages",
    "headers": { "x-api-key": "sk-ant-api03-..." },
    "body": { "model": "...", "system": "...", "messages": [...], "tools": [...] }
  },
  "response": {
    "status": 200,
    "headers": { ... },
    "body": { "id": "msg_...", "content": [...], "usage": { ... } },
    "sse_events": [ { "event": "message_start", "data": { ... } }, ... ]
  }
}
```

> API keys are automatically redacted in the trace (first 12 chars + `...`).

## Architecture

```
claude-tap/
├── claude_tap/
│   ├── __init__.py       # Core CLI: reverse proxy + Claude launcher
│   ├── __main__.py       # python -m claude_tap entry point
│   └── viewer.html       # Self-contained HTML viewer template
├── .github/workflows/
│   ├── ci.yml            # Lint + test on push/PR
│   └── publish.yml       # PyPI publish on tag
├── test_e2e.py           # End-to-end tests (5 test scenarios)
├── pyproject.toml        # Project metadata & dependencies
├── LICENSE               # MIT
└── .traces/              # Output directory (auto-created)
```

### Key Components

| Component | Description |
|-----------|-------------|
| `SSEReassembler` | Parses raw SSE byte stream and uses Anthropic SDK's `accumulate_event()` to reconstruct the full Message object |
| `TraceWriter` | Async-safe JSONL writer with `asyncio.Lock` |
| `proxy_handler` | aiohttp catch-all route that forwards requests and records responses |
| `run_claude` | Spawns Claude Code subprocess with correct env vars, forwards SIGINT |

### Design Decisions

- **HTTP not HTTPS**: The local proxy uses plain HTTP (`127.0.0.1`), avoiding TLS certificate complexity. Claude Code connects via `ANTHROPIC_BASE_URL`.
- **Zero-latency streaming**: SSE chunks are forwarded immediately via `resp.write(chunk)`, then buffered in parallel for recording.
- **SDK-based reassembly**: Uses `anthropic.lib.streaming._messages.accumulate_event()` for accurate Message reconstruction, not custom parsing.
- **Self-contained HTML**: The viewer template has zero external dependencies. Trace data is embedded directly into the HTML file.

## Testing

```bash
# Run all E2E tests (uses fake Claude + fake upstream, no real API calls)
uv run python test_e2e.py
```

Tests cover: basic streaming/non-streaming, upstream errors (5xx), malformed SSE, large payloads (100KB+), and concurrent requests.

## License

MIT
