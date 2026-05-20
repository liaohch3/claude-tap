# Use Cases: standalone-proxy-runner

## Actors

| Actor | Description |
|-------|-------------|
| Local developer | Runs Claude Code or Codex CLI through `coding-cli` and inspects generated trace logs. |
| Maintainer | Validates proxy correctness, redaction, and token accounting before release. |

---

## UC-001: Capture A Claude Code Session

**Actor:** Local developer
**Goal:** Run Claude Code normally while collecting structured Anthropic Messages logs.
**Preconditions:** `/Users/hezhang/repos/coding-cli` is installed locally, `claude` is installed/authenticated, and the working directory is safe for a Claude Code session.

### Journey

| Step | Screen / Context | Action | Expected State | Locator | State Marker |
|------|------------------|--------|----------------|---------|--------------|
| 1 | Command Context | Execute `coding-cli claude -- -p "Reply with exactly: HELLO"` | `coding-cli` starts reverse proxy and launches `claude` | CLI command | Process started |
| 2 | Proxy Runtime | Claude sends `/v1/messages` to local proxy | Proxy forwards request to detected Anthropic target | Trace log line | `request.path == "/v1/messages"` |
| 3 | Stream Runtime | Upstream returns streaming or non-streaming response | Response reaches Claude Code unchanged | Child stdout | Contains `HELLO` |
| 4 | Trace Sink | Proxy completes request recording | JSONL trace contains redacted request and response body | Trace JSONL | `response.status == 200` |
| 5 | Summary Output | Child exits | `coding-cli` prints API call and token summary | CLI stdout | `API calls: >= 1` |
| 6 | Filesystem | Developer opens trace directory | `trace_*.jsonl` and `trace_*.log` exist | Filesystem path | Files non-empty |

### Checkpoints

- After step 1: Assert child process argv preserves `-p` and prompt after `--`.
- After step 2: Assert `authorization` and `x-api-key` headers are redacted.
- After step 4: Assert Anthropic content blocks, usage, and any thinking blocks are preserved.
- After step 5: Assert exit code matches the child `claude` exit code.

### Edge Cases

- **EC-001:** `claude` missing -> Expected: concise error and no lingering proxy.
- **EC-002:** Upstream unreachable -> Expected: 502 trace record and non-zero child/session result.
- **EC-003:** Streaming response has thinking deltas -> Expected: reconstructed `thinking` block and visible thinking counters.

---

## UC-002: Capture A Codex CLI Session

**Actor:** Local developer
**Goal:** Run Codex CLI while collecting OpenAI Responses logs, including WebSocket sessions when Codex chooses that transport.
**Preconditions:** `/Users/hezhang/repos/coding-cli` is installed locally, `codex` is installed/authenticated, and either API-key or ChatGPT auth state is available.

### Journey

| Step | Screen / Context | Action | Expected State | Locator | State Marker |
|------|------------------|--------|----------------|---------|--------------|
| 1 | Command Context | Execute `coding-cli codex -- exec "Reply HELLO"` | `coding-cli` detects Codex target and launches `codex` | CLI command | Process started |
| 2 | Launcher | Codex receives local base URL config | Env and config override point to local proxy | Captured fake proc env | `OPENAI_BASE_URL` set |
| 3 | Proxy Runtime | Codex sends Responses HTTP/SSE or WebSocket traffic | Proxy forwards to OpenAI API or ChatGPT Codex backend | Trace JSONL | `request.path` includes `responses` |
| 4 | WebSocket Runtime | If WS is used, client/server events are relayed | Trace has request and response `ws_events` | Trace JSONL | `transport == "websocket"` |
| 5 | Trace Sink | Usage is normalized | Summary includes input/output/cache/reasoning when present | Summary output | `reasoning_tokens` key present when available |
| 6 | Shutdown | Codex exits | Proxy/session resources close cleanly | CLI exit code | Matches child exit code |

### Checkpoints

- After step 2: Assert user-provided `openai_base_url` override is not duplicated.
- After step 3: Assert ChatGPT auth target strips `/v1` only when required.
- After step 4: Assert `response.output_item.done` output is preserved even when `response.completed.output` is empty.
- After step 5: Assert `output_tokens_details.reasoning_tokens` maps to normalized `reasoning_tokens`.

### Edge Cases

- **EC-004:** Codex auth file is malformed -> Expected: fallback to OpenAI API target.
- **EC-005:** WebSocket upstream connect fails -> Expected: client sees 502 and trace records error.
- **EC-006:** Forward mode CA is untrusted -> Expected: failure explains `coding-cli trust-ca` or CA env requirements.

---

## UC-003: Run Proxy-Only Mode

**Actor:** Maintainer
**Goal:** Start a proxy without launching a child CLI so another terminal or script can connect manually.
**Preconditions:** `/Users/hezhang/repos/coding-cli` is installed locally.

### Journey

| Step | Screen / Context | Action | Expected State | Locator | State Marker |
|------|------------------|--------|----------------|---------|--------------|
| 1 | Command Context | Execute `coding-cli proxy --client claude --port 8080` | Proxy starts and prints client-specific instructions | CLI stdout | Contains `ANTHROPIC_BASE_URL` |
| 2 | Second Terminal | Execute `ANTHROPIC_BASE_URL=http://127.0.0.1:8080 claude -p "hi"` | Claude request goes through proxy | Trace JSONL | `/v1/messages` record |
| 3 | Proxy Runtime | Press Ctrl+C in proxy terminal | Proxy drains trace sink and exits | CLI exit | Exit clean |
| 4 | Filesystem | Inspect output dir | Trace and log files exist | Filesystem path | Files non-empty |

### Checkpoints

- After step 1: Assert printed instructions are specific to selected client.
- After step 3: Assert pending trace records are flushed before exit.

### Edge Cases

- **EC-007:** Port already in use -> Expected: clear bind error and no partial trace session.
- **EC-008:** Unsupported client value -> Expected: parser rejects value before starting proxy.

---

## UC-004: Validate Redaction And Token Accounting

**Actor:** Maintainer
**Goal:** Confirm logs are safe and useful after fake-upstream and real CLI runs.
**Preconditions:** At least one fake-upstream or real CLI trace exists.

### Journey

| Step | Screen / Context | Action | Expected State | Locator | State Marker |
|------|------------------|--------|----------------|---------|--------------|
| 1 | Test Context | Run default test suite | Unit and fake E2E tests pass | CLI command | pytest success |
| 2 | Filesystem | Search trace/log files for fake secret strings | No raw secret appears | Shell command | No matches |
| 3 | Trace JSONL | Inspect normalized usage fields | Token fields are present and numeric where provider exposed them | JSON path | `usage.input_tokens` |
| 4 | Trace JSONL | Inspect thinking metadata | Numeric reasoning tokens and visible thinking counters are separated | JSON path | `usage.reasoning_tokens` or visible counters |

### Checkpoints

- After step 1: Assert `uv run ruff check .`, `uv run ruff format --check .`, and `uv run pytest tests/ -x --timeout=60` pass.
- After step 2: Assert `authorization` and `x-api-key` values are redacted.
- After step 4: Assert no inferred Anthropic thinking-token count is presented as exact unless the provider exposed one.

### Edge Cases

- **EC-009:** Provider returns omitted thinking with no deltas -> Expected: no visible thinking chars, but output/reasoning usage preserved if numeric fields exist.
- **EC-010:** Provider returns malformed usage -> Expected: normalizer returns safe zero/default fields without crashing.

---

## Locator Index

| Locator | Element | Used In | Purpose |
|---------|---------|---------|---------|
| CLI command | Terminal command invocation | UC-001, UC-002, UC-003, UC-004 | Start and validate runner behavior |
| Trace JSONL | `trace_*.jsonl` records | UC-001, UC-002, UC-003, UC-004 | Verify captured API records |
| Proxy log line | `trace_*.log` entries | UC-001, UC-003 | Verify proxy diagnostics |
| Filesystem path | Output trace directory | UC-001, UC-003 | Verify persisted artifacts |
| Captured fake proc env | Test subprocess capture | UC-002 | Verify child env/config injection |
| JSON path | Record field assertion | UC-004 | Verify usage/redaction fields |

---

## UX Gap Report

### GAP-001
**Location:** CLI summary
**Issue:** The exact summary output format is not implemented yet, so tests need to drive field names before docs promise a stable machine-readable shape.
**Suggestion:** Define `TraceSummary` in `trace.py` and snapshot the printed summary plus optional summary JSON.

### GAP-002
**Location:** CA trust flow
**Issue:** Forward proxy failures can be hard to diagnose when a child binary ignores CA env vars.
**Suggestion:** Add `coding-cli trust-ca` and a targeted error hint that prints the CA path and relevant env vars.

---
*Generated: 2026-05-20T22:04:03Z*
*Last Updated: 2026-05-20T22:04:03Z*
*Mode: GENERATE*
