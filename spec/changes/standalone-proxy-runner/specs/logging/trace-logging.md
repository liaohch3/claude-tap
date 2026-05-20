# Efficient Trace Logging

## Requirement

The standalone project must write compact, append-only trace logs and proxy process logs efficiently, without generating HTML or retaining complete sessions in memory. Each trace record must be self-contained enough for later offline analysis.

## Scenarios

### Scenario: Record written after API call

**WHEN** an HTTP or WebSocket API interaction completes
**THEN** the trace writer appends exactly one compact JSON line
**AND** updates counters without reading the existing file.

### Scenario: Long session

**WHEN** a session contains many API calls
**THEN** memory usage grows only with active request/stream reconstruction
**AND** closed records remain on disk rather than in process memory.

### Scenario: Credentials in headers

**WHEN** request headers contain `authorization` or `x-api-key`
**THEN** the trace stores a redacted value
**AND** raw secret material does not appear in trace JSONL or proxy logs.

## Interface

### Props (if UI component)

Not applicable.

### API Contract (if endpoint)

| Surface | Method | Request | Response |
|---------|--------|---------|----------|
| Trace writer | `write(record)` | Structured trace record | JSONL append complete |
| Trace writer | `get_summary()` | None | Token/API-call summary |
| Trace writer | `close()` | None | File flushed and closed |

## Persistence (if applicable)

| Storage | Key | Value | Lifecycle |
|---------|-----|-------|-----------|
| Trace JSONL | compact JSON per line | Self-contained API records | Created per run |
| Proxy log | text log | Diagnostics only, no secrets | Created per run |
| Summary JSON | optional summary file | Counters and output paths | Written at shutdown if implemented |

---
*Spec for: standalone-proxy-runner*
*Created: 2026-05-20T22:04:03Z*
