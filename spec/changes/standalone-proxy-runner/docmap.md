# standalone-proxy-runner API/SDK Seed Pack

## Goal
- **Change:** `standalone-proxy-runner`
- **Project:** `claude-tap`
- **Generated:** `2026-05-20T22:04:03Z`
- **Purpose:** Seed later implementation with the external CLI/API contracts most likely to affect Claude Code and Codex CLI proxy compatibility, stream reconstruction, and reasoning/thinking token logging.

## Source Priority and Confidence
- Prefer official product/API docs for endpoint, auth, and streaming contracts.
- Prefer official CLI docs and official repositories for launch/auth behavior.
- Treat community reports as diagnostic hints only, not design authority.

Legend:
- `O` = official
- `F` = official forum
- `C` = community

## Most Likely APIs/SDKs (priority order)

| Priority | API/SDK or Contract | Why Likely Needed | Key Operations / Constraints | Source Links | Used In | Last Checked |
|----------|---------------------|-------------------|------------------------------|--------------|---------|--------------|
| P0 | OpenAI Responses API | Codex CLI API-key mode and Codex transport reconstruction center on Responses payloads. | `POST /v1/responses`; response objects include input, output, tools, `previous_response_id`, and usage fields including reasoning-related output details. | [Responses API](https://platform.openai.com/docs/api-reference/responses/object?lang=node.js) (`O`), [API request debugging](https://platform.openai.com/docs/api-reference/authentication?api-mode=responses) (`O`) | plan/apply/validate | 2026-05-20 |
| P0 | Codex CLI | The standalone runner must launch the official local Codex CLI without breaking its auth, config, or transport expectations. | Codex runs locally, supports npm/brew installs, prompts for ChatGPT or API-key auth, and is open source. | [Codex CLI docs](https://developers.openai.com/codex/cli) (`O`), [openai/codex](https://github.com/openai/codex) (`O`) | plan/apply/validate | 2026-05-20 |
| P0 | Claude Code settings and env | Claude Code reverse mode depends on `ANTHROPIC_BASE_URL` and settings env injection; forward mode depends on proxy env behavior. | `settings.json` supports `env`; Claude Code also documents environment variables and proxy-related settings. | [Claude Code settings](https://docs.anthropic.com/en/docs/claude-code/settings) (`O`) | plan/apply/validate | 2026-05-20 |
| P0 | Anthropic Messages API and extended thinking | Claude Code traffic uses Messages; thinking blocks and streaming deltas must be preserved when returned. | Messages responses include content blocks and usage; extended thinking can produce thinking content blocks and streaming `thinking_delta` events. | [Messages API](https://docs.anthropic.com/en/api/messages) (`O`), [Extended thinking](https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking) (`O`), [Streaming Messages](https://docs.anthropic.com/en/docs/build-with-claude/streaming) (`O`) | plan/apply/validate | 2026-05-20 |
| P1 | aiohttp client/server/WebSocket | Existing core proxy behavior is implemented with aiohttp; the standalone rewrite should keep one proven async HTTP stack. | `ClientSession` supports connection pooling, `trust_env`, HTTP requests, and `ws_connect`; server APIs provide `StreamResponse` and `WebSocketResponse`. | [aiohttp client reference](https://docs.aiohttp.org/en/stable/client_reference.html) (`O`), [aiohttp advanced client usage](https://docs.aiohttp.org/en/stable/client_advanced.html) (`O`), [aiohttp server reference](https://docs.aiohttp.org/en/stable/web_reference.html) (`O`) | apply/validate | 2026-05-20 |
| P1 | Python packaging | The standalone project needs a compact package boundary and reproducible development environment. | Use PEP 621 `pyproject.toml`, Python 3.11+, and minimal runtime dependencies. | [PyPA packaging guide](https://packaging.python.org/) (`O`), [PEP 621](https://peps.python.org/pep-0621/) (`O`) | apply | 2026-05-20 |

## API Context Cards

### OpenAI Responses API
- **Likely usage in this change:** Capture Codex API-key HTTP/SSE traffic and reconstruct Codex WebSocket response bodies into a durable trace shape.
- **Most relevant APIs/endpoints:** `POST /v1/responses`, `GET /v1/responses/{response_id}`, response object usage fields, streaming events, and request debugging headers.
- **Required call/data contract:** Preserve `input`, `instructions`, `tools`, `previous_response_id`, `output`, `usage`, and nested token details exactly as sent or received; do not drop response items with `type: "reasoning"`.
- **Important constraints:** Reasoning tokens can be represented under output token details or related response usage fields; request IDs and rate-limit headers are useful diagnostics and should remain in redacted response headers.
- **Failure patterns to expect:** 400 for invalid request shape, 401/403 for auth, 429 for rate limits, WebSocket fallback or transport errors in Codex-specific flows.
- **Best docs to read first:** Responses API object and API request debugging sections.
- **Sources:** [Responses API](https://platform.openai.com/docs/api-reference/responses/object?lang=node.js) (`O`), [API request debugging](https://platform.openai.com/docs/api-reference/authentication?api-mode=responses) (`O`)
- **Example sketch:** Local reverse proxy receives `/v1/responses`, forwards to OpenAI or ChatGPT Codex backend, records compact JSONL with `request.body`, `response.body`, response headers, `duration_ms`, and normalized usage.

### Codex CLI
- **Likely usage in this change:** Launch the existing `codex` binary through reverse or forward proxy mode while preserving config override and auth behavior.
- **Most relevant APIs/endpoints:** Official CLI command, local auth/config under Codex home, OpenAI API target for API key mode, ChatGPT Codex backend target for ChatGPT auth mode.
- **Required call/data contract:** Keep argv boundaries intact; inject `OPENAI_BASE_URL` and `-c openai_base_url=...` for reverse mode unless the user already supplied an override; inject CA env vars for forward mode.
- **Important constraints:** The CLI evolves regularly, and transport can use HTTP/SSE or WebSocket; tests must catch URL construction and WebSocket reconstruction regressions.
- **Failure patterns to expect:** Missing `codex` binary, auth file unreadable, base URL override ignored by a CLI release, custom CA not accepted, WebSocket connection blocked by proxy settings.
- **Best docs to read first:** Official Codex CLI page and the `openai/codex` repository README/changelog.
- **Sources:** [Codex CLI docs](https://developers.openai.com/codex/cli) (`O`), [openai/codex](https://github.com/openai/codex) (`O`)
- **Example sketch:** `agent-tap codex -- exec "hello"` starts a local proxy, injects local OpenAI base URL, launches Codex, and writes `.traces/YYYY-MM-DD/trace_HHMMSS.jsonl`.

### Claude Code Settings and Anthropic Messages
- **Likely usage in this change:** Launch `claude` while capturing Messages requests/responses and preserving thinking blocks, tool calls, and usage.
- **Most relevant APIs/endpoints:** `ANTHROPIC_BASE_URL`, settings `env`, Messages API, streaming Messages, extended thinking.
- **Required call/data contract:** Preserve content block order, including `thinking`, `redacted_thinking`, `tool_use`, and `text`; accumulate `thinking_delta` and `signature_delta` stream data.
- **Important constraints:** Modern Claude models may return summarized or omitted thinking; omitted thinking can still be billed without streaming thinking text, so logs must distinguish numeric token data from visible thinking content.
- **Failure patterns to expect:** Missing `claude` binary, conflicting settings base URL, proxy env not propagated into subprocess settings, streaming connection closed before final usage delta.
- **Best docs to read first:** Claude Code settings, Messages API, Extended thinking, and Streaming Messages.
- **Sources:** [Claude Code settings](https://docs.anthropic.com/en/docs/claude-code/settings) (`O`), [Messages API](https://docs.anthropic.com/en/api/messages) (`O`), [Extended thinking](https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking) (`O`)
- **Example sketch:** `agent-tap claude -- --model claude-sonnet-4-6` injects `ANTHROPIC_BASE_URL` through Claude settings/env, captures `/v1/messages`, and writes JSONL records with SSE event arrays.

### aiohttp Proxy Runtime
- **Likely usage in this change:** Implement the standalone async reverse proxy, upstream client, and WebSocket relay without adding a heavier framework.
- **Most relevant APIs/endpoints:** `ClientSession`, `ClientSession.request`, `ClientSession.ws_connect`, `web.Application`, `web.StreamResponse`, `web.WebSocketResponse`.
- **Required call/data contract:** Use one shared upstream `ClientSession` per run, set `auto_decompress=False` when forwarding raw bytes, and use `trust_env=True` so user network proxy settings continue to work.
- **Important constraints:** `ws_connect` proxy behavior deserves explicit tests; no body-size limit should be applied to API payloads.
- **Failure patterns to expect:** TLS verification failures, timeout while reading streams, broken client connections, WebSocket close races.
- **Best docs to read first:** aiohttp client reference and advanced proxy docs.
- **Sources:** [aiohttp client reference](https://docs.aiohttp.org/en/stable/client_reference.html) (`O`), [aiohttp advanced client usage](https://docs.aiohttp.org/en/stable/client_advanced.html) (`O`)
- **Example sketch:** Create one `aiohttp.ClientSession(auto_decompress=False, trust_env=True)` and pass it into both reverse and forward proxy handlers.

## Command-to-API Mapping

| Command/Phase Surface | External Dependencies | Required Checks |
|-----------------------|-----------------------|-----------------|
| `agent-tap claude` reverse mode | Claude Code settings, Anthropic Messages | Verify `ANTHROPIC_BASE_URL`/settings injection, `/v1/messages` forwarding, SSE thinking accumulation, auth redaction. |
| `agent-tap codex` reverse mode | Codex CLI, OpenAI Responses, ChatGPT Codex backend | Verify target detection, `OPENAI_BASE_URL`, `openai_base_url` config override, `/v1` strip behavior, WebSocket capture. |
| `agent-tap codex --proxy-mode forward` | Codex CLI, local CA, aiohttp/asyncio TLS bridge | Verify `HTTPS_PROXY`, `SSL_CERT_FILE`, `CODEX_CA_CERTIFICATE`, CONNECT/TLS interception, WebSocket relay. |
| Trace summary | OpenAI/Anthropic usage schemas | Verify input/output/cache/reasoning counters, best-effort visible thinking flags, and no raw credentials. |

## Seed Links by Planning Phase

### Phase 2 (Deep Research)
- [OpenAI Responses API](https://platform.openai.com/docs/api-reference/responses/object?lang=node.js)
- [Claude Code settings](https://docs.anthropic.com/en/docs/claude-code/settings)
- [Anthropic extended thinking](https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking)
- [aiohttp advanced client usage](https://docs.aiohttp.org/en/stable/client_advanced.html)

### Phase 5 (Approach Tradeoffs)
- [Codex CLI official docs](https://developers.openai.com/codex/cli)
- [openai/codex repository](https://github.com/openai/codex)
- [Anthropic Streaming Messages](https://docs.anthropic.com/en/docs/build-with-claude/streaming)

### Phase 6/8 (Design + Tasks)
- [OpenAI API request debugging](https://platform.openai.com/docs/api-reference/authentication?api-mode=responses)
- [aiohttp client reference](https://docs.aiohttp.org/en/stable/client_reference.html)
- [PyPA packaging guide](https://packaging.python.org/)

## Agent Follow-Up Prompts

1. "Verify current Codex CLI transport behavior for API-key and ChatGPT auth modes, then update the URL construction tests for the standalone runner."
2. "Design the standalone trace schema for Anthropic Messages, OpenAI Responses HTTP/SSE, and Codex WebSocket events with reasoning-token and visible-thinking fields."
3. "Implement minimal Claude Code launch support from existing env/settings injection tests, excluding viewer/dashboard/update code."
4. "Implement minimal Codex launch support with reverse proxy, forward proxy CA injection, target detection, and WebSocket capture tests."

## Quick Recheck Before Coding

- Recheck Codex CLI docs/release notes for base URL, WebSocket, and CA env behavior.
- Recheck Anthropic extended thinking docs for model-specific `thinking` and `display` behavior.
- Recheck OpenAI Responses usage schema for reasoning-token field placement.
- Confirm local authenticated `claude` and `codex` availability before running real E2E.
