# standalone-proxy-runner - Implementation Tasks

## Status: Ready for implementation

## Progress

- Total: 43 tasks
- Completed: 0
- Remaining: 43

## Tasks

### 1. Foundation & Setup

- [ ] 1.1 Create the separate repository `/Users/hezhang/repos/coding-cli`
  - **What**: Initialize a clean git repository and root project files.
  - **Completion Criteria**: `git status` works in `/Users/hezhang/repos/coding-cli` and no files import from `claude_tap`.
  - **Verify**: `cd /Users/hezhang/repos/coding-cli && git status --short`
- [ ] 1.2 Add package metadata `/Users/hezhang/repos/coding-cli/pyproject.toml`
  - **What**: Configure Python 3.11+, `src` layout, console script `coding-cli`, dependencies, ruff, and pytest.
  - **Completion Criteria**: `uv sync --extra dev` creates a working environment.
  - **Verify**: `uv run coding-cli --help`
- [ ] 1.3 Add root docs `/Users/hezhang/repos/coding-cli/README.md`
  - **What**: Document install, `coding-cli claude`, `coding-cli codex`, output files, and security/redaction note.
  - **Completion Criteria**: README contains no viewer/dashboard references.
  - **Verify**: `grep -F "coding-cli codex" README.md`
- [ ] 1.4 Add package shell `/Users/hezhang/repos/coding-cli/src/coding_cli/__init__.py` and `/Users/hezhang/repos/coding-cli/src/coding_cli/__main__.py`
  - **What**: Provide importable package and `python -m coding_cli`.
  - **Completion Criteria**: Module invocation prints CLI help.
  - **Verify**: `uv run python -m coding_cli --help`
- [ ] 1.5 Add test scaffolding `/Users/hezhang/repos/coding-cli/tests/`
  - **What**: Configure pytest fixtures for temp trace dirs and fake upstreams.
  - **Completion Criteria**: Empty smoke test passes.
  - **Verify**: `uv run pytest tests/ -q`

**Checkpoint:** Repository installs locally and `coding-cli --help` works without touching network or old `claude_tap` modules.

### 2. CLI and Client Launch

- [ ] 2.1 Implement CLI parser `/Users/hezhang/repos/coding-cli/src/coding_cli/cli.py`
  - **What**: Support `claude`, `codex`, `proxy`, and `trust-ca` command surfaces with `--` passthrough.
  - **Completion Criteria**: Unknown runner flags fail clearly; child args remain unmodified after `--`.
  - **Verify**: `uv run pytest tests/test_cli.py -q`
- [ ] 2.2 Implement client specs `/Users/hezhang/repos/coding-cli/src/coding_cli/clients.py`
  - **What**: Model only Claude Code and Codex CLI config.
  - **Completion Criteria**: Tests assert no extra clients exist.
  - **Verify**: `uv run pytest tests/test_clients.py -q`
- [ ] 2.3 Implement Claude target detection
  - **What**: Read `ANTHROPIC_BASE_URL`, project `.claude/settings.local.json`, project `.claude/settings.json`, and user settings in precedence order.
  - **Completion Criteria**: Unit tests cover env, project, user, and fallback targets.
  - **Verify**: `uv run pytest tests/test_clients.py::test_claude_target_detection -q`
- [ ] 2.4 Implement Codex target detection
  - **What**: Read `CODEX_HOME/auth.json` or `~/.codex/auth.json` and choose OpenAI API vs ChatGPT Codex backend.
  - **Completion Criteria**: Unit tests cover ChatGPT auth, missing auth, malformed auth, and API-key fallback.
  - **Verify**: `uv run pytest tests/test_clients.py::test_codex_target_detection -q`
- [ ] 2.5 Implement child env injection
  - **What**: Set reverse base URL env/config and forward proxy/CA env for each client.
  - **Completion Criteria**: Tests assert `ANTHROPIC_BASE_URL`, `OPENAI_BASE_URL`, Codex `-c openai_base_url`, `HTTPS_PROXY`, `SSL_CERT_FILE`, and `CODEX_CA_CERTIFICATE`.
  - **Verify**: `uv run pytest tests/test_launch.py -q`
- [ ] 2.6 Implement subprocess lifecycle `/Users/hezhang/repos/coding-cli/src/coding_cli/session.py`
  - **What**: Resolve binary, launch child without shell joining, preserve TUI foreground process behavior, handle Ctrl+C, and return child exit code.
  - **Completion Criteria**: Tests use fake child processes to verify argv/env/exit code.
  - **Verify**: `uv run pytest tests/test_session.py -q`

**Checkpoint:** Fake `claude` and `codex` binaries receive correct argv/env and exit codes flow back to `coding-cli`.

### 3. Trace, Usage, and Stream Reconstruction

- [ ] 3.1 Implement trace schema and redaction `/Users/hezhang/repos/coding-cli/src/coding_cli/proxy/records.py`
  - **What**: Build records with redacted sensitive headers and normalized usage.
  - **Completion Criteria**: Raw `authorization` and `x-api-key` values never appear in records.
  - **Verify**: `uv run pytest tests/test_records.py -q`
- [ ] 3.2 Implement queue-backed trace sink `/Users/hezhang/repos/coding-cli/src/coding_cli/trace.py`
  - **What**: Append compact JSONL through an async queue, drain on shutdown, and expose summary counters.
  - **Completion Criteria**: Tests prove records are written, counters update, and close drains pending records.
  - **Verify**: `uv run pytest tests/test_trace.py -q`
- [ ] 3.3 Implement usage normalizer `/Users/hezhang/repos/coding-cli/src/coding_cli/usage.py`
  - **What**: Normalize prompt/completion aliases, cache aliases, OpenAI nested reasoning tokens, and visible thinking counters.
  - **Completion Criteria**: Tests cover Anthropic, OpenAI Responses, Chat Completions, and WebSocket reconstructed usage.
  - **Verify**: `uv run pytest tests/test_usage.py -q`
- [ ] 3.4 Implement SSE reassembler `/Users/hezhang/repos/coding-cli/src/coding_cli/streams/sse.py`
  - **What**: Reconstruct Anthropic Messages and OpenAI Responses/Chat Completions streams.
  - **Completion Criteria**: Tests cover text deltas, thinking deltas, signature deltas, tool JSON deltas, usage-only chunks, malformed SSE, and chunk boundaries.
  - **Verify**: `uv run pytest tests/test_sse.py -q`
- [ ] 3.5 Add trace summary output
  - **What**: Print API calls, input/output/cache/reasoning totals, visible thinking counts, trace path, and log path at shutdown.
  - **Completion Criteria**: CLI summary tests assert output and optional summary JSON if implemented.
  - **Verify**: `uv run pytest tests/test_summary.py -q`

**Checkpoint:** Trace/usage/SSE tests pass without any proxy server running.

### 4. Reverse Proxy

- [ ] 4.1 Implement reverse proxy server `/Users/hezhang/repos/coding-cli/src/coding_cli/proxy/reverse.py`
  - **What**: Create aiohttp app with no body size limit, allowed path gate, upstream forwarding, and shared session.
  - **Completion Criteria**: Fake upstream receives forwarded requests and trace records are written.
  - **Verify**: `uv run pytest tests/test_reverse_proxy.py -q`
- [ ] 4.2 Implement allowed path policy
  - **What**: Include only Claude/Codex API prefixes needed for `/v1/messages`, `/v1/responses`, `/responses`, `/v1/chat/completions`, `/chat/completions`, `/v1/models`, and related Codex paths.
  - **Completion Criteria**: Tests block scanner-like paths and allow expected API paths.
  - **Verify**: `uv run pytest tests/test_path_policy.py -q`
- [ ] 4.3 Implement request body decoding compatibility
  - **What**: Preserve raw bytes for upstream while parsing JSON/text for trace records.
  - **Completion Criteria**: Tests cover JSON, empty body, text body, gzip/deflate where supported, and zstd if dependency retained.
  - **Verify**: `uv run pytest tests/test_reverse_proxy.py::test_request_body_shapes -q`
- [ ] 4.4 Implement streaming response relay
  - **What**: Relay SSE chunks immediately while feeding reassembler and writing final record after completion.
  - **Completion Criteria**: Fake streaming upstream output reaches client and reconstructed body lands in trace.
  - **Verify**: `uv run pytest tests/test_reverse_proxy.py::test_streaming_sse_capture -q`
- [ ] 4.5 Implement upstream error handling
  - **What**: Return 502 and write error traces on connection failures.
  - **Completion Criteria**: Error record includes status and no raw credentials.
  - **Verify**: `uv run pytest tests/test_reverse_proxy.py::test_upstream_error -q`

**Checkpoint:** Reverse proxy captures fake Claude and fake OpenAI HTTP/SSE traffic end-to-end.

### 5. Codex WebSocket Capture

- [ ] 5.1 Implement WebSocket relay `/Users/hezhang/repos/coding-cli/src/coding_cli/proxy/websocket.py`
  - **What**: Connect upstream before accepting client upgrade and relay text/binary/control frames.
  - **Completion Criteria**: Fake WS upstream and client exchange messages through proxy.
  - **Verify**: `uv run pytest tests/test_websocket_proxy.py::test_websocket_proxy_basic -q`
- [ ] 5.2 Implement request event reconstruction
  - **What**: Merge incremental `response.create` messages, preserving input, tools, previous response IDs, and function outputs.
  - **Completion Criteria**: Tests cover multiple request messages and exact duplicate list entries.
  - **Verify**: `uv run pytest tests/test_websocket_proxy.py::test_request_reconstruction -q`
- [ ] 5.3 Implement response body reconstruction
  - **What**: Merge `response.completed` and `response.output_item.done` events into useful `output` and `usage`.
  - **Completion Criteria**: Tests cover empty completed output with output item events.
  - **Verify**: `uv run pytest tests/test_websocket_proxy.py::test_response_reconstruction -q`
- [ ] 5.4 Implement WebSocket failure records
  - **What**: Write one trace record when upstream WS connection fails before client upgrade.
  - **Completion Criteria**: Client sees 502 and trace records error.
  - **Verify**: `uv run pytest tests/test_websocket_proxy.py::test_websocket_upstream_failure -q`

**Checkpoint:** Codex WebSocket sessions are captured into one durable JSONL record per session.

### 6. Forward Proxy and Certificates

- [ ] 6.1 Implement CA helper `/Users/hezhang/repos/coding-cli/src/coding_cli/certs.py`
  - **What**: Generate/load local CA, restrict key permissions, include SKI/AKI extensions, and generate cached per-host server certs.
  - **Completion Criteria**: Python 3.13 cert tests pass.
  - **Verify**: `uv run pytest tests/test_certs.py -q`
- [ ] 6.2 Implement explicit trust command
  - **What**: Add `coding-cli trust-ca` for macOS current-user keychain trust or printed platform instructions.
  - **Completion Criteria**: Tests verify command construction does not use sudo and no trust happens silently.
  - **Verify**: `uv run pytest tests/test_trust_ca.py -q`
- [ ] 6.3 Implement CONNECT/TLS forward proxy `/Users/hezhang/repos/coding-cli/src/coding_cli/proxy/forward.py`
  - **What**: Accept CONNECT, terminate TLS with local certs, parse tunneled HTTP requests, and forward upstream.
  - **Completion Criteria**: Fake HTTPS upstream test captures request/response trace.
  - **Verify**: `uv run pytest tests/test_forward_proxy.py::test_forward_proxy_connect -q`
- [ ] 6.4 Implement forward streaming capture
  - **What**: Relay chunked SSE through CONNECT tunnel while reconstructing response body.
  - **Completion Criteria**: Fake upstream streaming test records SSE events and summary usage.
  - **Verify**: `uv run pytest tests/test_forward_proxy.py::test_forward_proxy_streaming -q`
- [ ] 6.5 Implement forward WebSocket capture
  - **What**: Relay WebSocket upgrades inside CONNECT tunnel and reuse WebSocket reconstruction helpers.
  - **Completion Criteria**: Fake WS over forward proxy test writes reconstructed trace.
  - **Verify**: `uv run pytest tests/test_forward_proxy.py::test_forward_proxy_websocket -q`
- [ ] 6.6 Implement proxy-only command
  - **What**: `coding-cli proxy --client claude|codex` starts a proxy without launching a child and prints exact env/config instructions.
  - **Completion Criteria**: Tests assert printed instructions and clean Ctrl+C shutdown.
  - **Verify**: `uv run pytest tests/test_proxy_command.py -q`

**Checkpoint:** Reverse and forward proxy modes both capture fake HTTP/SSE/WebSocket flows.

### 7. Integration, E2E, and Polish

- [ ] 7.1 Add fake Claude CLI integration test
  - **What**: Fake `claude` reads `ANTHROPIC_BASE_URL`, sends non-streaming and streaming `/v1/messages`, and exits.
  - **Completion Criteria**: Trace has two records and summary prints two API calls.
  - **Verify**: `uv run pytest tests/test_e2e_fake_claude.py -q`
- [ ] 7.2 Add fake Codex HTTP/SSE integration test
  - **What**: Fake `codex` reads `OPENAI_BASE_URL` and config override, sends `/v1/responses`, and exits.
  - **Completion Criteria**: URL strip and trace usage are correct.
  - **Verify**: `uv run pytest tests/test_e2e_fake_codex.py -q`
- [ ] 7.3 Add fake Codex WebSocket integration test
  - **What**: Fake `codex` opens WS through the local proxy and exchanges Responses events.
  - **Completion Criteria**: Trace includes `transport: websocket`, request events, response events, output, and usage.
  - **Verify**: `uv run pytest tests/test_e2e_fake_codex_ws.py -q`
- [ ] 7.4 Add gated real Claude Code smoke test
  - **What**: Run only with `--run-real-e2e` and installed/authenticated `claude`.
  - **Completion Criteria**: Captures at least one `/v1/messages` record or skips with clear reason.
  - **Verify**: `uv run pytest tests/e2e/ --run-real-e2e --client claude --timeout=300`
- [ ] 7.5 Add gated real Codex CLI smoke test
  - **What**: Run only with `--run-real-e2e` and installed/authenticated `codex`.
  - **Completion Criteria**: Captures HTTP/SSE or WebSocket Responses traffic or skips with clear reason.
  - **Verify**: `uv run pytest tests/e2e/ --run-real-e2e --client codex --timeout=300`
- [ ] 7.6 Add security regression test
  - **What**: Search trace/log outputs for raw fake API keys and bearer tokens.
  - **Completion Criteria**: Tests fail if raw secrets are present.
  - **Verify**: `uv run pytest tests/test_redaction.py -q`
- [ ] 7.7 Add lint/format/test gate docs
  - **What**: Document `uv run ruff check .`, `uv run ruff format --check .`, and `uv run pytest tests/ -x --timeout=60`.
  - **Completion Criteria**: README and/or CONTRIBUTING has the commands.
  - **Verify**: `grep -F "uv run pytest tests/" README.md`
- [ ] 7.8 Run full local gates
  - **What**: Execute lint, format check, and all default tests in `/Users/hezhang/repos/coding-cli`.
  - **Completion Criteria**: All pass.
  - **Verify**: `uv run ruff check . && uv run ruff format --check . && uv run pytest tests/ -x --timeout=60`

**Checkpoint:** The separate repo is ready for a PR with fake E2E evidence and documented real E2E status.

## Dependencies

- Tasks 1.1-1.5 must complete before implementation modules and tests.
- Tasks 2.1-2.6 unlock proxy integration because sessions need client/env behavior.
- Tasks 3.1-3.5 can run in parallel with client launch after project setup.
- Reverse proxy tasks should precede forward proxy tasks because they define the shared record and stream behavior.
- WebSocket reconstruction can be built in parallel with reverse proxy once records and trace sink exist.
- Real E2E tasks require fake E2E and full local gates first.

## Implementation Notes

- Use current `claude-tap` files only as references; do not import from `claude_tap`.
- Keep comments sparse and focused on TLS, process-group, and stream reconstruction edge cases.
- Prefer structured helpers over string manipulation for JSON and URL handling.
- Keep command examples and code/comments in English.
- If real E2E cannot run due missing local auth, record that in validation notes rather than weakening fake-upstream coverage.

---
*Generated: 2026-05-20T22:04:03Z*
*From design: design.md*
*Last Updated: 2026-05-20T22:04:03Z*
