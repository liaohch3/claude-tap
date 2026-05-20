# standalone-proxy-runner: Core CLI Proxy Capture Flows

## Overview
This code map covers the parts of `claude-tap` that should inform the standalone rewrite: client launch, reverse proxy forwarding, SSE reconstruction, WebSocket capture, forward proxy TLS interception, trace writing, and token summary collection.
Notable flows: launch environment injection [1a->1d], streaming trace reconstruction [2a->2e], Codex WebSocket reconstruction [3a->3d], forward proxy TLS capture [4a->4e]

---

## Trace 1: Client Launch - CLI starts Claude Code or Codex through a local proxy
**Description:** The CLI resolves the selected client, prepares proxy environment/config overrides, launches the child process, and later summarizes trace output.

```
async_main session lifecycle <-- claude_tap/cli.py:554
├── client config matrix <-- claude_tap/cli.py:122
├── trace/log paths and writer <-- claude_tap/cli.py:554
├── proxy server selection <-- claude_tap/cli.py:612
├── child env injection <-- claude_tap/cli.py:239
└── summary and viewer generation <-- claude_tap/cli.py:722
    └── token counters printed <-- claude_tap/cli.py:739
```

### 1a: Client Config Matrix
**Path:** `claude_tap/cli.py:122`
**Description:** The current config matrix includes Claude Code and Codex settings that can be retained while dropping all other client entries.

### 1b: Trace Paths and Writer
**Path:** `claude_tap/cli.py:554`
**Description:** `async_main` creates dated trace/log paths and constructs `TraceWriter` before starting either proxy mode.

### 1c: Proxy Server Selection
**Path:** `claude_tap/cli.py:612`
**Description:** Forward mode creates a `ForwardProxyServer`, while reverse mode creates an aiohttp app around `proxy_handler`.

### 1d: Child Env Injection
**Path:** `claude_tap/cli.py:239`
**Description:** `run_client` injects proxy variables, reverse base URL settings, Codex config overrides, CA env vars, and foreground TUI process control.

### 1e: Summary and Nonessential Viewer Generation
**Path:** `claude_tap/cli.py:722`
**Description:** The existing shutdown path closes the writer, generates HTML, registers manifests, cleans traces, and prints summary output, so the standalone rewrite should retain only close and summary.

---

## Trace 2: Reverse Proxy HTTP/SSE - Request is forwarded and recorded
**Description:** Reverse proxy mode allows base-URL-compatible clients to send API requests to the local aiohttp handler.

```
proxy_handler entry <-- claude_tap/proxy.py:137
├── allowed-path gate <-- claude_tap/proxy.py:141
├── WebSocket branch <-- claude_tap/proxy.py:145
├── upstream URL and body parse <-- claude_tap/proxy.py:154
├── streaming branch <-- claude_tap/proxy.py:231
│   └── SSE reassembly/write <-- claude_tap/proxy.py:258
└── non-streaming branch <-- claude_tap/proxy.py:321
    └── trace record build <-- claude_tap/proxy.py:376
```

### 2a: Allowed Path Gate
**Path:** `claude_tap/proxy.py:137`
**Description:** The reverse proxy rejects paths outside known API prefixes before forwarding or recording.

### 2b: Upstream URL and Request Capture
**Path:** `claude_tap/proxy.py:154`
**Description:** The handler strips configured path prefixes, reads and parses the body, tracks turns, and prepares upstream headers.

### 2c: Streaming SSE Reassembly
**Path:** `claude_tap/proxy.py:258`
**Description:** Streaming responses are relayed chunk-by-chunk while `SSEReassembler` reconstructs a full response body for the trace.

### 2d: Non-Streaming Capture
**Path:** `claude_tap/proxy.py:321`
**Description:** Non-streaming responses are read, decompressed for JSON parsing where needed, recorded, and returned to the client unchanged.

### 2e: Trace Record Shape
**Path:** `claude_tap/proxy.py:376`
**Description:** `_build_record` stores timestamp, request metadata, redacted request headers, response metadata, optional stream events, and upstream base URL.

---

## Trace 3: Codex WebSocket - Responses traffic is relayed and reconstructed
**Description:** Codex can use WebSocket transport for Responses flows, so the proxy records request and server events and reconstructs useful request/response bodies.

```
reverse WS branch <-- claude_tap/ws_proxy.py:60
├── upstream ws URL construction <-- claude_tap/ws_proxy.py:67
├── bidirectional relay <-- claude_tap/ws_proxy.py:144
├── record builder <-- claude_tap/ws_proxy.py:223
└── body reconstruction <-- claude_tap/ws_proxy.py:289
    └── output item merge <-- claude_tap/ws_proxy.py:338
```

### 3a: Reverse WebSocket Branch
**Path:** `claude_tap/ws_proxy.py:60`
**Description:** `_handle_websocket` maps the incoming local URL to an upstream WebSocket URL and connects before accepting the client upgrade.

### 3b: Bidirectional Relay
**Path:** `claude_tap/ws_proxy.py:144`
**Description:** Client and upstream messages are relayed concurrently while text messages are retained for trace reconstruction.

### 3c: WebSocket Record Builder
**Path:** `claude_tap/ws_proxy.py:223`
**Description:** `_build_ws_record` stores WebSocket transport metadata, parsed request events, parsed response events, and reconstructed response body.

### 3d: Responses Body Reconstruction
**Path:** `claude_tap/ws_proxy.py:338`
**Description:** `_reconstruct_ws_response_body` merges `response.completed` and `response.output_item.done` events so Codex tool/message output remains visible in logs.

---

## Trace 4: Forward Proxy - CONNECT/TLS interception captures clients without base URL support
**Description:** Forward proxy mode uses local CA certificates and CONNECT interception to capture HTTPS traffic while preserving normal child CLI network behavior.

```
forward server startup <-- claude_tap/cli.py:612
├── CA ensure/trust gate <-- claude_tap/cli.py:567
├── CONNECT handling <-- claude_tap/forward_proxy.py:126
├── tunneled HTTP parse <-- claude_tap/forward_proxy.py:342
├── upstream forward/write <-- claude_tap/forward_proxy.py:386
└── tunneled WebSocket branch <-- claude_tap/forward_proxy.py:617
```

### 4a: CA Ensure and Trust
**Path:** `claude_tap/cli.py:567`
**Description:** Forward mode creates or loads a local CA before startup and optionally ensures macOS user-keychain trust for clients that need it.

### 4b: Forward Proxy Lifecycle
**Path:** `claude_tap/forward_proxy.py:126`
**Description:** `ForwardProxyServer` owns the TCP server, CA, writer, upstream session, local reverse bridge configuration, and active client tasks.

### 4c: Tunneling Request Parse
**Path:** `claude_tap/forward_proxy.py:342`
**Description:** The TLS tunnel parser reads request line, headers, and body, then chooses WebSocket relay or HTTP forwarding.

### 4d: Streaming and Non-Streaming Writes
**Path:** `claude_tap/forward_proxy.py:386`
**Description:** `_forward_and_record` records upstream errors, streaming responses, and non-streaming responses through the same trace record shape.

### 4e: Raw WebSocket Relay
**Path:** `claude_tap/forward_proxy.py:617`
**Description:** The forward proxy implements a lower-level WebSocket handshake/relay path for upgrades that happen inside the CONNECT tunnel.

---

## Trace 5: Trace and Token Accounting - JSONL logs are written and summarized
**Description:** Trace writing is already append-only and concurrency-safe, but the standalone rewrite needs richer reasoning/thinking token normalization.

```
TraceWriter init <-- claude_tap/trace.py:16
├── JSONL append and flush <-- claude_tap/trace.py:34
├── usage extraction <-- claude_tap/trace.py:52
├── provider usage normalization <-- claude_tap/usage.py:6
└── SSE thinking accumulation <-- claude_tap/sse.py:98
    └── chat reasoning mirror <-- claude_tap/sse.py:172
```

### 5a: Append-Only Writer
**Path:** `claude_tap/trace.py:16`
**Description:** `TraceWriter` opens the trace path once, serializes compact JSON lines under an async lock, and updates counters per record.

### 5b: Usage Extraction
**Path:** `claude_tap/trace.py:52`
**Description:** The current summary accumulates input, output, cache read, and cache creation tokens from normalized response usage.

### 5c: Usage Normalization Gap
**Path:** `claude_tap/usage.py:6`
**Description:** `normalize_usage` maps cache and prompt/completion aliases but does not yet promote nested reasoning-token fields into a summary counter.

### 5d: Anthropic Thinking Stream Capture
**Path:** `claude_tap/sse.py:98`
**Description:** Anthropic `thinking_delta` events are accumulated into `thinking` content blocks when streaming returns them.

### 5e: OpenAI Chat Reasoning Mirror
**Path:** `claude_tap/sse.py:172`
**Description:** Chat Completion `reasoning_content` deltas are mirrored to a synthetic `thinking` content block for downstream renderers and logs.

---

## Code Snippets from Codemap Files

### cli.py
```python
# Lines 122-143: Claude Code and Codex client config entries to extract.
CLIENT_CONFIGS: dict[str, ClientConfig] = {
    "claude": ClientConfig(
        cmd="claude",
        label="Claude Code",
        install_url="https://docs.anthropic.com/en/docs/claude-code",
        base_url_env="ANTHROPIC_BASE_URL",
        base_url_suffix="",
        default_target="https://api.anthropic.com",
        nesting_env_keys=("CLAUDECODE", "CLAUDE_CODE_SSE_PORT"),
        inject_settings_env=True,
    ),
    "codex": ClientConfig(
```

### proxy.py
```python
# Lines 137-152: Reverse proxy entry path, allowlist, and WebSocket handoff.
async def proxy_handler(request: web.Request) -> web.StreamResponse:
    # Reject requests to unknown paths (scanner/crawler protection)
    ctx: dict = request.app["trace_ctx"]
    extra_prefixes: tuple[str, ...] = ctx.get("extra_allowed_path_prefixes", ())
    if not _is_allowed_path(request.path, extra_prefixes):
        log.debug(f"Blocked non-API path: {request.method} {request.path}")
        return web.Response(status=404, text="Not Found")

    # Detect WebSocket upgrade and route to WS proxy.
    if request.headers.get("Upgrade", "").lower() == "websocket":
```

### sse.py
```python
# Lines 98-108: Anthropic thinking and tool JSON deltas are accumulated.
            elif event_type == "content_block_delta":
                idx = data.get("index", 0)
                delta = data.get("delta", {})
                if idx < len(self._snapshot.get("content", [])):
                    block = self._snapshot["content"][idx]
                    if delta.get("type") == "text_delta":
                        block["text"] = block.get("text", "") + delta.get("text", "")
                    elif delta.get("type") == "thinking_delta":
                        block["thinking"] = block.get("thinking", "") + delta.get("thinking", "")
                    elif delta.get("type") == "input_json_delta":
```

### ws_proxy.py
```python
# Lines 338-394: Codex WebSocket response body reconstruction.
def _reconstruct_ws_response_body(ws_events: list[dict]) -> dict | None:
    """Build a best-effort response body from WS events.

    Recent Codex versions may emit multiple response.completed events and keep
    the actual assistant text inside response.output_item.done rather than the
    terminal response payload. Reconstruct a richer body for traces/viewer use.
    """
    merged: dict | None = None
    output_items: dict[int, dict] = {}
```

### trace.py
```python
# Lines 34-44: Compact JSONL append and optional live broadcast.
    async def write(self, record: dict) -> None:
        """Write a record and update statistics."""
        async with self._lock:
            self._file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            self._file.flush()
            self.count += 1
            self._update_stats(record)

        # Broadcast to live viewer if enabled
        if self._live_server:
```

---

*Generated from standalone-proxy-runner Codemap*
*Core CLI/proxy/logging Implementation*
*Date: 2026-05-20T22:04:03Z*
