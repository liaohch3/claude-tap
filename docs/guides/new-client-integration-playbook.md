# Playbook: Adding a New LLM Client to claude-tap

Distilled from the Codex integration (PR #12, 2026-02-28). Use this as a repeatable
framework for adding support for any new LLM client (e.g., Gemini CLI, Grok CLI, etc.).

---

## Phase 1: Reconnaissance — Understand the Client's Wire Protocol

Before writing code, answer these questions:

1. **What API endpoint does the client call?** (e.g., `api.openai.com/v1/responses`,
   `api.anthropic.com/v1/messages`)
2. **Does it have alternative endpoints?** (e.g., Codex uses `chatgpt.com/backend-api/codex`
   for ChatGPT Plus users, NOT `api.openai.com`)
3. **What transport does it use?** HTTP POST? WebSocket? gRPC?
   - **Lesson from Codex**: Codex v0.106.0 silently switched from HTTP to WebSocket
     for `/v1/responses`. The HTTP proxy saw nothing. Always verify the actual wire
     transport, not what the docs say.
4. **What env var controls the base URL?** (`OPENAI_BASE_URL`, `ANTHROPIC_BASE_URL`, etc.)
5. **Does the child process actually inherit that env var?** (Codex has a Rust subprocess
   that may or may not respect the Node.js parent's env)
6. **What encoding/compression does it use?** (Codex sends zstd-compressed bodies)

### How to investigate

```bash
# Watch actual network traffic
lsof -i -P | grep <process_name>

# Check what the process sees
ps -p <pid> -E | tr ' ' '\n' | grep BASE_URL

# Intercept with mitmproxy for full visibility
mitmproxy --mode reverse:https://api.example.com --listen-port 8080
```

**Key principle**: Don't trust documentation. Observe actual behavior.

---

## Phase 2: Proxy Wiring — Make Every Request Visible

### Checklist

- [ ] Set the correct env var to redirect traffic to claude-tap's local proxy
- [ ] Handle path mapping (client sends `/v1/responses`, upstream expects `/responses`)
- [ ] Handle request body encoding (zstd, gzip, etc.)
- [ ] Handle response streaming (SSE events, chunked transfer)
- [ ] **Pin transport to HTTP if using reverse proxy** — disable WebSocket/gRPC features
      that bypass the HTTP proxy
- [ ] Verify with actual traffic (not just unit tests)

### Validation checkpoint

```bash
# Run the client through claude-tap
uv run python -m claude_tap --tap-client <name> -- <simple prompt>

# Check trace file has actual API calls (not just /models)
wc -l .traces/trace_*.jsonl  # Should be > 1
python3 -c "
import json
with open('.traces/trace_<latest>.jsonl') as f:
    for line in f:
        d = json.loads(line)
        print(d['request']['path'], d['response']['status'])
"
```

**If trace only shows 1 line (models/health check): the real API calls are bypassing
your proxy.** Stop and investigate transport.

---

## Phase 3: Viewer Compatibility — Every API Format Is Different

Each LLM provider has a different response format. Map these fields:

| Concept | Claude (Chat Completions) | OpenAI (Responses API) | Your Client |
|---------|--------------------------|----------------------|-------------|
| System prompt | `body.system` | `body.instructions` | ? |
| Messages | `body.messages[]` | `body.input[]` | ? |
| Message content | `{type: "text", text}` | `{type: "input_text", text}` | ? |
| Token usage | `response.body.usage` | SSE `response.completed` event | ? |
| Response output | `response.body.content` | SSE `response.output_text.delta` | ? |
| Tools | `body.tools[]` | `body.tools[]` | ? |

### Viewer fix pattern

1. Find every place the viewer reads Claude-specific fields
2. Add a normalize function that maps the new format to a common structure
3. Keep backward compatibility — old traces must still render correctly

### Validation checkpoint

Open the HTML viewer with a real trace and verify:
- [ ] System prompt displayed
- [ ] Messages rendered with correct roles
- [ ] Token counts non-zero
- [ ] Diff between turns works
- [ ] Response output shown

---

## Phase 4: Recording — Evidence Is Part of the Deliverable

Every new client integration needs:

1. **Terminal recording**: Real E2E session showing the client running through claude-tap
   - Tool: `asciinema rec` → `agg` (GIF) → `ffmpeg` (MP4)
   - Must show: startup banner, at least 2-3 turns, tool calls if applicable

2. **Viewer recording**: Playwright-automated walkthrough of the HTML viewer
   - Tool: Playwright `record_video_dir` → `.webm` → `ffmpeg` (MP4)
   - Must show: system prompt, messages, tokens, diff

3. **Screenshots**: Static evidence for PR review
   - At least: overview, messages, diff, token stats

**Use real trace data, not mocks.** Reviewers can tell the difference.

### PR screenshots gotcha

Use absolute URLs in PR descriptions (`raw.githubusercontent.com/...`), not relative
paths. GitHub PR bodies don't resolve relative image paths.

---

## Phase 5: Defensive Design — What Will Break Next?

After the integration works, anticipate future breakage:

- **Client updates transport**: Pin transport explicitly, don't assume HTTP forever.
  Document the workaround AND file a TODO for native support.
- **Client changes API format**: Keep normalize functions isolated so they're easy to update.
- **Client adds auth complexity**: Document required scopes/permissions in the plan doc.

### Always leave behind

- [ ] Error experience doc if you hit non-obvious issues
- [ ] TODO/plan doc for known limitations (e.g., WebSocket native support)
- [ ] Tests that catch the specific failure mode you discovered

---

## Anti-Patterns (from Codex integration)

| Anti-pattern | What happened | Lesson |
|-------------|---------------|--------|
| Trusting the obvious API endpoint | Assumed `api.openai.com`, actual was `chatgpt.com/backend-api/codex` | Always trace actual network calls |
| Assuming HTTP transport | Codex uses WebSocket by default, proxy saw nothing | Verify wire transport, pin if needed |
| Recording demos before fixing data | First recordings showed 0 tokens (403 errors in trace) | Fix data pipeline first, record last |
| Using old trace data for screenshots | Screenshots showed errors from pre-fix traces | Always re-record after fixes |
| Relative image paths in PR body | All images broken on GitHub | Use `raw.githubusercontent.com` absolute URLs |

---

## Template: New Client Checklist

```markdown
## Adding support for: <CLIENT_NAME>

### Recon
- [ ] Identified API endpoint(s)
- [ ] Identified transport (HTTP/WS/gRPC)
- [ ] Identified env var for base URL
- [ ] Verified child process inherits env var
- [ ] Identified request encoding

### Proxy
- [ ] Env var injection working
- [ ] Path mapping correct
- [ ] Request decompression working
- [ ] Response streaming captured
- [ ] Transport pinned to HTTP (if reverse proxy)
- [ ] Trace file has real API calls (not just models)

### Viewer
- [ ] System prompt displayed
- [ ] Messages rendered
- [ ] Token counts non-zero
- [ ] Diff working
- [ ] Response output shown
- [ ] Old Claude traces still work

### Evidence
- [ ] Terminal recording (≥3 turns)
- [ ] Viewer recording
- [ ] Screenshots (≥5)
- [ ] PR description with absolute image URLs

### Docs
- [ ] Error experience (if applicable)
- [ ] TODO for known limitations
- [ ] Checklist template used
```
