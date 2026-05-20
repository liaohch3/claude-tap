# Requirements for standalone-proxy-runner

## Category

IMPLEMENTATION

## Summary

Create a clean, minimum, standalone Python project in `/Users/hezhang/repos/coding-cli` that rewrites the core proxy runner needed to launch Claude Code and Codex CLI, route them through a local proxy, and collect efficient structured logs including reasoning/thinking token data where the upstream exposes it. The new repository should intentionally exclude the HTML viewer, dashboard, multi-client surface, public docs machinery, update command, and other nonessential pieces from `claude-tap`.

## Success Criteria

- [ ] A fresh user can run Claude Code through the standalone project and receive the same CLI behavior while structured trace logs are written.
- [ ] A fresh user can run Codex CLI through the standalone project in API-key and ChatGPT/OAuth-oriented modes without breaking request routing.
- [ ] Reverse proxy captures Anthropic Messages and OpenAI Responses HTTP/SSE traffic with reconstructed request, response, headers, timings, and normalized usage.
- [ ] WebSocket capture remains available for Codex Responses flows that use WebSocket transport.
- [ ] Trace summaries include input, output, cache read, cache write, and reasoning/thinking token counters when present in the response payload.
- [ ] Logs are efficient enough for long sessions: append-only JSONL, minimal flush overhead, no generated HTML, and no in-memory retention of full session history beyond active stream reconstruction.
- [ ] The package has focused tests for launch env injection, URL construction, HTTP/SSE/WebSocket capture, redaction, token normalization, and graceful shutdown.

## Scope

### In Scope

- A standalone Python repository at `/Users/hezhang/repos/coding-cli` with its own `pyproject.toml`, CLI entry point, tests, and README.
- Two supported clients only: `claude` for Claude Code and `codex` for Codex CLI.
- Reverse proxy mode for base-URL-compatible flows.
- Forward proxy mode with CONNECT/TLS interception only where it is necessary for CLI compatibility.
- Local CA generation and trust-root injection for child processes that need forward proxy capture.
- Structured trace files and process/proxy log files.
- Usage normalization for Anthropic Messages and OpenAI Responses/Chat Completions shapes, including OpenAI `output_tokens_details.reasoning_tokens` and Anthropic thinking content blocks.
- Explicit safety behavior: redact credentials, avoid shell interpolation, preserve subprocess exit codes, and clean up proxy/session resources.

### Out of Scope

- The current `viewer.html`, live viewer, dashboard, HTML export, screenshots, and browser UI.
- Support for Gemini, Kimi, OpenCode, Pi, Hermes, Cursor, or other clients.
- Auto-update, changelog automation, marketing docs, public bilingual docs, or README image assets.
- Persistent database storage, cloud sync, hosted log ingestion, or remote telemetry.
- Reusing large current modules verbatim when a small rewrite is clearer.
- Modifying Claude Code or Codex CLI internals.

## Context

### Problem Statement

`claude-tap` has grown into a broad multi-client trace viewer. The user now wants a separate repository that keeps the hard-won proxy/launch/logging behavior for Claude Code and Codex CLI, but removes everything that is not essential to reliable CLI execution and efficient log collection.

### User Impact

Maintainers and agent engineers get a compact project that is easier to audit, test, run in automation, and evolve around proxy correctness and token accounting. It reduces review surface by separating core capture from UI and multi-provider features.

### Codebase Findings

- `claude_tap.cli.CLIENT_CONFIGS` defines per-client launch metadata; Claude Code and Codex entries are the relevant subset for the new project.
- `claude_tap.cli.run_client` injects reverse proxy base URLs and forward proxy environment variables, including Codex-specific `SSL_CERT_FILE` and `CODEX_CA_CERTIFICATE`.
- `claude_tap.cli.async_main` currently mixes core session lifecycle with live viewer, update checks, manifest registration, cleanup, and HTML export.
- `claude_tap.proxy.proxy_handler` handles reverse proxy forwarding, path allowlisting, streaming reconstruction, and trace record creation.
- `claude_tap.forward_proxy.ForwardProxyServer` handles CONNECT/TLS interception and records HTTP, SSE, and WebSocket traffic.
- `claude_tap.sse.SSEReassembler` already accumulates Anthropic thinking deltas and OpenAI Responses/Chat Completions stream shapes.
- `claude_tap.ws_proxy` records Codex WebSocket traffic and reconstructs Responses request/response bodies from incremental messages.
- `claude_tap.trace.TraceWriter` is already append-only JSONL, but it does not yet expose reasoning/thinking token totals in summaries.

## Clarifications

### Questions Asked

1. Where should the standalone project live? -> User clarified it will be a separate repository at `/Users/hezhang/repos/coding-cli`.
2. Which clients are required? -> User specified Claude Code and Codex CLI only.
3. Should the UI be kept? -> User explicitly said no UI or nonessential system parts.
4. Which logs matter? -> Assumption: keep raw structured request/response JSONL plus a concise process/proxy log and summary counters.
5. What does "thinking tokens" mean across providers? -> Assumption: collect numeric reasoning-token fields when exposed, preserve Anthropic thinking blocks or summarized thinking text when returned, and separately report best-effort thinking content presence when a provider bills thinking through output tokens without a separate counter.
6. Should the rewrite preserve every current client mode? -> Assumption: preserve only modes needed for Claude Code and Codex CLI reliability: reverse base URL, forward proxy with local CA, Codex target auto-detection, and Codex WebSocket capture.
7. Should implementation prioritize smallest diff or clean design? -> User approved the clean architecture approach.

### Assumptions Made

- The first implementation target is a separate local repository at `/Users/hezhang/repos/coding-cli`; it may reference this repository as source evidence during implementation but must not import `claude_tap`.
- Python remains the implementation language because the current proven proxy logic is Python/aiohttp and the user asked for robust log collection rather than a new platform experiment.
- The standalone CLI uses `coding-cli` as the working command name and `coding_cli` as the Python package name unless implementation discovers a conflict.
- The project should default to no browser open, no HTML generation, no live server, and no generated assets.
- Efficient logging means no full-session buffering and no expensive post-processing in the critical path.

## Technical Context

- Key files: `claude_tap/cli.py`, `claude_tap/proxy.py`, `claude_tap/forward_proxy.py`, `claude_tap/ws_proxy.py`, `claude_tap/sse.py`, `claude_tap/trace.py`, `claude_tap/usage.py`, `claude_tap/certs.py`.
- Existing patterns: dataclass client config, `aiohttp.ClientSession(auto_decompress=False, trust_env=True)`, append-only JSONL writer, path allowlist, redacted request headers, SSE reassembly, WebSocket request/response reconstruction.
- Dependencies: Python 3.11+, `aiohttp`, `cryptography`, optional `backports-zstd` if compressed upstream support remains required.
- Constraints: preserve CLI TUI behavior, avoid leaking proxy logs into the child TUI, handle Ctrl+C gracefully, redact authorization headers, support Python 3.13-sensitive TLS certificate behavior, and keep URL construction testable.

## Testing Requirements

- Unit tests for CLI argument parsing, client target detection, env injection, and subprocess argv construction.
- Unit tests for usage normalization, including OpenAI `output_tokens_details.reasoning_tokens` and Anthropic thinking content detection.
- Proxy integration tests with fake Anthropic and OpenAI upstreams for non-streaming, SSE, and WebSocket traffic.
- Forward proxy tests for CONNECT/TLS lifecycle and CA injection where feasible.
- Real E2E smoke tests gated behind installed/authenticated `claude` and `codex` CLIs, with retained trace artifacts on failure.
- Regression tests that raw API keys are not written to trace files.

## Forbidden Actions

- Do not carry over the HTML viewer, live viewer, dashboard, export command, update command, or multi-client documentation.
- Do not introduce a database, background daemon, or hosted service.
- Do not shell-join user arguments; subprocess launch must preserve argv boundaries.
- Do not log unredacted `authorization`, `x-api-key`, or local auth token contents.
- Do not discard WebSocket support unless current Codex transport behavior is explicitly verified to no longer need it.
- Do not depend on undocumented UI state from Claude Code or Codex CLI.

---

**Generated from:** User input + codebase exploration + clarification assumptions
**Clarification rule:** Clarifications are recorded with assumptions because the user requested a clean minimum direction and no unresolved question blocks safe planning.

---
*Generated: 2026-05-20T22:04:03Z*
