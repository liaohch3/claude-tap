# standalone-proxy-runner - Technical Design

## Context

`claude-tap` currently proves the hard parts of the problem: launching Claude Code and Codex CLI through a local proxy, forwarding HTTP/SSE/WebSocket traffic, reconstructing streamed payloads, generating local CA certificates for forward proxy mode, and writing JSONL traces. That behavior is spread through `claude_tap/cli.py`, `proxy.py`, `forward_proxy.py`, `ws_proxy.py`, `sse.py`, `trace.py`, `usage.py`, and `certs.py`, while the runtime also carries viewer generation, live browser state, dashboard/export commands, update checks, manifest cleanup, and a broad client matrix.

The approved approach is **Clean Architecture**. The implementation will create a separate repository at `/Users/hezhang/repos/coding-cli` with a clear module split around CLI orchestration, client launch configuration, proxy transports, stream reconstruction, trace persistence, certificate handling, and usage normalization. Existing `claude-tap` code is evidence and test inspiration, not a package dependency.

## Goals / Non-Goals

**Goals:**
- Create `/Users/hezhang/repos/coding-cli` as a standalone Python 3.11+ repository with command `coding-cli` and package `coding_cli`.
- Support exactly two clients: Claude Code (`claude`) and Codex CLI (`codex`).
- Preserve reliable reverse proxy, forward proxy, SSE, and Codex WebSocket capture.
- Write efficient local logs: compact JSONL trace records, proxy/process log text, and optional summary JSON.
- Normalize input, output, cache read, cache write, and reasoning/thinking token fields where providers expose them.
- Keep CLI TUI behavior intact and preserve child process exit codes.
- Test URL construction, launch env injection, redaction, stream reconstruction, WebSocket reconstruction, CA generation, and graceful shutdown.

**Non-Goals:**
- No HTML viewer, live viewer, dashboard, export command, screenshot evidence, or browser UI.
- No support for Gemini, Kimi, OpenCode, Pi, Hermes, Cursor, Qoder, Agy, or future clients during initial implementation.
- No hosted telemetry, database, daemon, or cloud sync.
- No dependency on `claude_tap` imports from the new repository.
- No attempt to modify Claude Code or Codex CLI internals.

## Design Decisions

### Decision 1: Separate Repository With Clean Module Boundaries

**Choice:** Create `/Users/hezhang/repos/coding-cli` with package `coding_cli`, console script `coding-cli`, and modules organized by domain instead of copying `claude_tap` as-is.

**Rationale:**
- The user explicitly clarified the tool will be a separate repo.
- The current `async_main` mixes core lifecycle with live viewer, update checks, HTML generation, manifest registration, and cleanup at `claude_tap/cli.py:554` and `claude_tap/cli.py:722`.
- Trade-off accepted: more upfront rewrite work than copying files, but a much smaller long-term surface.

**Alternatives Considered:**
- Minimal copy from `claude_tap`: rejected because it would preserve viewer/multi-client coupling.
- Subproject inside `claude-tap`: rejected because the user clarified the destination is `/Users/hezhang/repos/coding-cli`.

### Decision 2: Narrow Client Registry

**Choice:** Implement a two-entry client registry for `claude` and `codex`, using typed client specs for command name, base URL env, default target, target detector, reverse path stripping, and CA env needs.

**Rationale:**
- Claude and Codex are the only requested clients.
- Current `ClientConfig` already proves the necessary fields for Claude and Codex at `claude_tap/cli.py:122`.
- Trade-off accepted: adding new clients later requires deliberate new specs/tests instead of a generic matrix now.

**Alternatives Considered:**
- Keep a generic client framework from the current repo: rejected because broad provider support is out of scope.
- Hard-code launch logic in command handlers: rejected because tests need to validate env/config behavior independently.

### Decision 3: Transport Layer Split Into Reverse, Forward, and WebSocket Components

**Choice:** Create dedicated transport modules:
- `coding_cli/proxy/reverse.py` for aiohttp reverse HTTP/SSE/WebSocket entry.
- `coding_cli/proxy/forward.py` for CONNECT/TLS interception.
- `coding_cli/proxy/websocket.py` for shared Codex WebSocket reconstruction helpers.
- `coding_cli/proxy/records.py` for trace record construction and header redaction.

**Rationale:**
- Reverse HTTP/SSE flow is distinct from CONNECT/TLS handling in the current code (`claude_tap/proxy.py:137`, `claude_tap/forward_proxy.py:126`).
- Codex WebSocket reconstruction is important enough to keep shared and transport-independent (`claude_tap/ws_proxy.py:223`).
- Trade-off accepted: a few more files than the current shape, but each file has one reason to change.

**Alternatives Considered:**
- One large `proxy.py`: rejected because it would recreate the current coupling.
- Only reverse proxy mode: rejected because forward proxy and CA injection are needed for some CLI/auth flows.

### Decision 4: Queue-Backed Trace Sink With Stable Record Schema

**Choice:** Implement `TraceSink` as a small async queue-backed writer that serializes compact JSONL records, drains on shutdown, and maintains aggregate counters. Each record keeps raw provider payloads plus a normalized top-level `usage` summary.

**Rationale:**
- Current `TraceWriter` proves compact JSONL and summary counters (`claude_tap/trace.py:16`), but writes directly under the proxy path and lacks reasoning-token totals.
- A queue separates proxy latency from disk writes while staying simple and local.
- Trade-off accepted: shutdown must drain the queue carefully, but tests can exercise that boundary.

**Alternatives Considered:**
- Direct flush-per-record file writes: simpler but more coupled to proxy latency.
- SQLite or structured database: rejected as nonessential and heavier than append-only JSONL.

### Decision 5: Explicit Usage Normalization for Reasoning/Thinking

**Choice:** Add a `UsageSummary` normalizer that maps provider usage fields to shared names:
- `input_tokens`
- `output_tokens`
- `cache_read_input_tokens`
- `cache_creation_input_tokens`
- `reasoning_tokens`
- `visible_thinking_blocks`
- `visible_thinking_chars`

**Rationale:**
- Current normalization maps prompt/completion and cache aliases but misses nested reasoning-token promotion (`claude_tap/usage.py:6`).
- Anthropic thinking may be returned as content blocks or deltas, while billed thinking can be included in output tokens without a separate numeric field.
- Trade-off accepted: visible thinking counts are not token counts; the summary must label them separately to avoid false precision.

**Alternatives Considered:**
- Infer Anthropic thinking tokens by local tokenization: rejected because tokenizer mismatch would produce misleading accounting.
- Only store raw usage: rejected because the user requested efficient logs with thinking-token accounting.

### Decision 6: Tests First Around Contracts, Then Real E2E

**Choice:** Build a fake-upstream integration test suite before real E2E, then gate real Claude/Codex smoke tests behind explicit flags and installed/authenticated CLIs.

**Rationale:**
- Existing fake-upstream tests cover the core end-to-end proxy path without paid credentials (`tests/test_e2e.py:218`).
- Existing real E2E is skipped by default and requires installed/authenticated Claude CLI (`tests/e2e/test_real_proxy.py:1`).
- Trade-off accepted: real E2E still depends on local auth and may be documented as skipped when unavailable.

**Alternatives Considered:**
- Only unit tests: rejected because proxy URL construction and streaming behavior need integration coverage.
- Always run real E2E in default tests: rejected because it would make local development brittle.

## Architecture

### Component Overview

```
/Users/hezhang/repos/coding-cli
├── pyproject.toml
├── README.md
├── src/coding_cli/
│   ├── __main__.py
│   ├── cli.py
│   ├── clients.py
│   ├── session.py
│   ├── certs.py
│   ├── usage.py
│   ├── trace.py
│   ├── streams/
│   │   └── sse.py
│   └── proxy/
│       ├── records.py
│       ├── reverse.py
│       ├── forward.py
│       └── websocket.py
└── tests/
    ├── test_clients.py
    ├── test_usage.py
    ├── test_trace.py
    ├── test_reverse_proxy.py
    ├── test_forward_proxy.py
    ├── test_websocket_proxy.py
    └── e2e/
```

### Data Flow

1. `coding-cli claude|codex -- ...` parses runner flags and passes everything after `--` unchanged to the child CLI; `coding-cli proxy --client claude|codex` starts the same proxy/session stack without launching a child.
2. `clients.py` resolves client config and upstream target.
3. `session.py` creates trace paths, logging, upstream `aiohttp.ClientSession`, `TraceSink`, and the selected proxy server.
4. The launcher injects reverse base URL or forward proxy/CA env into the child process and preserves argv boundaries.
5. Proxy transport forwards HTTP/SSE/WebSocket traffic to upstream and builds redacted trace records.
6. Stream helpers reconstruct Anthropic/OpenAI/Codex streamed payloads.
7. `usage.py` normalizes usage and visible thinking metadata.
8. `TraceSink` appends JSONL, updates counters, drains on shutdown, and returns a summary.
9. `session.py` stops proxy resources, closes the upstream session, closes the trace sink, prints summary, and returns the child exit code.

### Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `/Users/hezhang/repos/coding-cli/pyproject.toml` | Create | Package metadata, dependencies, console script, ruff/pytest config |
| `/Users/hezhang/repos/coding-cli/README.md` | Create | Minimal install/run docs for Claude and Codex capture |
| `/Users/hezhang/repos/coding-cli/src/coding_cli/__main__.py` | Create | `python -m coding_cli` entry |
| `/Users/hezhang/repos/coding-cli/src/coding_cli/cli.py` | Create | Arg parsing and command dispatch |
| `/Users/hezhang/repos/coding-cli/src/coding_cli/clients.py` | Create | Claude/Codex specs, target detection, env injection helpers |
| `/Users/hezhang/repos/coding-cli/src/coding_cli/session.py` | Create | Run lifecycle, proxy startup, child process control, cleanup |
| `/Users/hezhang/repos/coding-cli/src/coding_cli/certs.py` | Create | Local CA and per-host cert generation |
| `/Users/hezhang/repos/coding-cli/src/coding_cli/trace.py` | Create | Queue-backed JSONL trace sink and summaries |
| `/Users/hezhang/repos/coding-cli/src/coding_cli/usage.py` | Create | Usage normalization and visible-thinking analysis |
| `/Users/hezhang/repos/coding-cli/src/coding_cli/streams/sse.py` | Create | Anthropic/OpenAI SSE reconstruction |
| `/Users/hezhang/repos/coding-cli/src/coding_cli/proxy/records.py` | Create | Redaction, record construction, shared constants |
| `/Users/hezhang/repos/coding-cli/src/coding_cli/proxy/reverse.py` | Create | Reverse HTTP/SSE/WebSocket proxy |
| `/Users/hezhang/repos/coding-cli/src/coding_cli/proxy/forward.py` | Create | CONNECT/TLS forward proxy |
| `/Users/hezhang/repos/coding-cli/src/coding_cli/proxy/websocket.py` | Create | Codex WebSocket relay/reconstruction helpers |
| `/Users/hezhang/repos/coding-cli/tests/**` | Create | Focused unit, fake-upstream, and gated real E2E tests |

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Codex transport behavior changes | Medium | High | Keep WebSocket and HTTP/SSE tests; add real Codex smoke test behind auth flag |
| Forward proxy TLS behavior differs across platforms | Medium | High | Keep Python 3.13 cert tests, use SKI/AKI extensions, document CA trust flow |
| Queue-backed trace sink drops records on interruption | Medium | High | Drain queue during shutdown, add tests for pending writes and cancellation |
| Thinking-token accounting could imply false precision | Medium | Medium | Separate numeric `reasoning_tokens` from visible thinking block/char counters |
| Separate repo loses useful fixtures from `claude-tap` | Low | Medium | Copy only focused fake upstream fixtures/tests into `coding-cli` |
| Real E2E unavailable locally due missing auth | Medium | Medium | Gate real tests explicitly and document skipped risk in PR/validation notes |

## Open Questions

- Final package description and PyPI/distribution target are deferred until after local implementation.
- Whether forward mode should auto-trust a CA on macOS is deferred; initial clean design should provide explicit `coding-cli trust-ca` or printed instructions instead of mutating trust silently.
- Whether to write `summary_*.json` by default or only print summary is deferred to implementation, but the trace sink should expose the data either way.

## Validation

### Rubric Scores

| Criteria | Score | Notes |
|----------|-------|-------|
| Groundedness | 1.00 | All major decisions cite current source files or explicit user clarification. |
| Correctness | 0.99 | Covers launch, proxy, WebSocket, trace, token, and test paths. |
| Simplicity | 0.99 | Separate modules are domain-based and avoid UI/multi-client extras. |
| Elegance | 0.99 | Clean boundaries isolate process, transport, stream, and persistence concerns. |
| Conformity | 0.99 | Reuses proven repo patterns while obeying the separate-repo clarification. |
| Clarity | 1.00 | Files, flows, and risks are concrete enough for task generation. |

### Iteration Log

- Iteration 1: Updated the plan from pragmatic local project to clean separate repository after user approval. Scores exceed 0.98 after aligning repository path, CLI name, module boundaries, and token-accounting language.

## Plan Review Summary

### Critical Issues (Must Fix)

None.

### Important Issues (Should Fix)

- The design originally implied a proxy-only command in the proposal but did not explicitly include it in data flow. Fixed by adding `coding-cli proxy --client claude|codex` to the session flow.
- CA trust behavior could become surprising if implementation auto-mutates the macOS keychain. Fixed by making explicit trust or printed instructions the initial design preference.

### Suggestions (Consider)

- Keep the first implementation README intentionally short and put deeper protocol notes in code comments or tests, not user docs.
- Add a `doctor` command only after the core CLI is stable; it is useful but not part of the first cut.

### What's Good

- The plan isolates high-risk proxy/TLS/WebSocket behavior behind testable modules.
- The trace schema keeps raw provider payloads while adding normalized usage for efficient analysis.
- The separate-repo target removes accidental dependencies on viewer and multi-client code.

### Verdict

Ready.

---
*Generated: 2026-05-20T22:04:03Z*
*Approach: clean*
