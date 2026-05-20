# Forward Proxy Capture

## Requirement

The standalone forward proxy must support CONNECT/TLS interception for Claude Code and Codex flows that cannot be captured with reverse base URL injection alone. It must generate and reuse a local CA, inject child trust roots, relay HTTP/SSE/WebSocket traffic, and record the same trace shape as reverse mode.

## Scenarios

### Scenario: CONNECT request captures HTTPS API traffic

**WHEN** a child CLI connects through `HTTPS_PROXY`
**THEN** the forward proxy accepts CONNECT, terminates TLS with a generated per-host certificate, forwards the plaintext HTTP request upstream, and records a trace.

### Scenario: Upstream request fails

**WHEN** the forward proxy cannot reach upstream
**THEN** it returns `502 Bad Gateway`
**AND** writes an error trace record with redacted request headers.

### Scenario: Shutdown with active clients

**WHEN** the parent command exits or is interrupted
**THEN** the forward proxy closes server sockets, client writers, and active tasks within a bounded timeout.

## Interface

### Props (if UI component)

Not applicable.

### API Contract (if endpoint)

| Surface | Method | Request | Response |
|---------|--------|---------|----------|
| Forward proxy | CONNECT | `host:443` | TLS interception tunnel |
| Forward proxy | HTTP absolute-form | `METHOD http(s)://host/path` | Upstream response passthrough or 502 |
| Certificate helper | function | CA directory | `(ca_cert_path, ca_key_path)` |

## Persistence (if applicable)

| Storage | Key | Value | Lifecycle |
|---------|-----|-------|-----------|
| CA directory | `ca.pem` | Local root certificate | Created once and reused |
| CA directory | `ca-key.pem` | Local private key with restricted permissions | Created once and reused |
| Memory | host certificate cache | Per-host cert/key pairs | Cleared when process exits |

---
*Spec for: standalone-proxy-runner*
*Created: 2026-05-20T22:04:03Z*
