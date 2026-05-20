# Codex WebSocket Capture

## Requirement

The standalone proxy must relay and record Codex WebSocket Responses traffic in reverse and forward proxy modes. It must preserve client and server events, reconstruct the most complete request body, reconstruct useful response output from `response.completed` and `response.output_item.done` events, and record failures.

## Scenarios

### Scenario: WebSocket session succeeds

**WHEN** Codex opens a WebSocket through the proxy
**THEN** the proxy connects upstream, accepts the client upgrade, relays messages bidirectionally, and writes one WebSocket trace record when the session closes.

### Scenario: Incremental request messages

**WHEN** Codex sends multiple `response.create` messages with input, tools, or tool outputs
**THEN** the trace request body merges those messages in order without dropping later meaningful values.

### Scenario: Completed response output is sparse

**WHEN** `response.completed` has empty `output` but `response.output_item.done` carries assistant or tool output
**THEN** the reconstructed response body includes the ordered output items.

## Interface

### Props (if UI component)

Not applicable.

### API Contract (if endpoint)

| Endpoint | Method | Request | Response |
|----------|--------|---------|----------|
| `/v1/responses` or upstream Codex WS path | WEBSOCKET | Codex Responses WebSocket events | Upstream WebSocket events |
| WebSocket record builder | function | client messages, server messages, headers | Structured trace record |

## Persistence (if applicable)

| Storage | Key | Value | Lifecycle |
|---------|-----|-------|-----------|
| Trace JSONL | one line per WebSocket session | Request/response `ws_events`, reconstructed bodies, duration | Written at session close or connection failure |

---
*Spec for: standalone-proxy-runner*
*Created: 2026-05-20T22:04:03Z*
