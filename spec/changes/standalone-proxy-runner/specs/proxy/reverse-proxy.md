# Reverse Proxy Capture

## Requirement

The standalone reverse proxy must forward supported Claude Code and Codex HTTP/SSE API traffic to the configured upstream while recording redacted request metadata, response metadata, reconstructed response bodies, stream events, timings, upstream target, and normalized usage.

## Scenarios

### Scenario: Non-streaming request

**WHEN** a supported non-streaming API request is sent to the local reverse proxy
**THEN** the proxy forwards the request to upstream
**AND** writes one JSONL record with request body, redacted request headers, response body, status, timing, and upstream base URL.

### Scenario: Streaming request

**WHEN** the upstream returns SSE chunks
**THEN** the proxy relays chunks to the client as they arrive
**AND** reconstructs a complete response snapshot for the trace after the stream closes.

### Scenario: Unknown path

**WHEN** the local reverse proxy receives a path outside the allowed Claude/Codex API prefixes
**THEN** it returns 404
**AND** does not forward or write a trace record.

## Interface

### Props (if UI component)

Not applicable.

### API Contract (if endpoint)

| Endpoint | Method | Request | Response |
|----------|--------|---------|----------|
| `/{path}` | Any | Claude/Codex API request under allowed prefixes | Upstream response passthrough |
| `/{path}` | Any | Unsupported path | `404 Not Found` |

## Persistence (if applicable)

| Storage | Key | Value | Lifecycle |
|---------|-----|-------|-----------|
| Trace JSONL | one line per API call | Redacted structured trace record | Written after response or stream completion |
| Proxy log | turn log entries | Diagnostics, upstream errors, timings | Written during run |

---
*Spec for: standalone-proxy-runner*
*Created: 2026-05-20T22:04:03Z*
