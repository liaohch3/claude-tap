# Codex CLI Launcher

## Requirement

The standalone CLI must launch Codex CLI through the local proxy while supporting API-key and ChatGPT/OAuth-oriented modes. It must preserve argv boundaries, detect the appropriate upstream target where feasible, inject reverse proxy base URL configuration, and provide forward proxy CA environment variables for transports that require HTTPS proxy capture.

## Scenarios

### Scenario: API-key reverse mode

**WHEN** the user runs Codex with API-key auth
**THEN** the child receives `OPENAI_BASE_URL=http://127.0.0.1:<port>/v1`
**AND** the child argv includes `-c openai_base_url="http://127.0.0.1:<port>/v1"` unless the user supplied that override.

### Scenario: ChatGPT auth target detection

**WHEN** the Codex auth file indicates ChatGPT auth mode
**THEN** the upstream target is `https://chatgpt.com/backend-api/codex`
**AND** `/v1` is stripped from local reverse proxy paths before upstream forwarding when required.

### Scenario: Forward proxy CA injection

**WHEN** Codex is launched in forward proxy mode
**THEN** the child receives `HTTPS_PROXY`, `SSL_CERT_FILE`, and `CODEX_CA_CERTIFICATE`
**AND** the command reports the local CA path without exposing credentials.

## Interface

### Props (if UI component)

Not applicable.

### API Contract (if endpoint)

| Surface | Method | Request | Response |
|---------|--------|---------|----------|
| CLI | command | `agent-tap codex -- [codex args]` | Exit code from child process |
| Launcher | function | `run_client(client="codex", proxy_mode, extra_args)` | Child exit code |
| Target detection | function | `CODEX_HOME` or `~/.codex/auth.json` | OpenAI API or ChatGPT Codex backend target |

## Persistence (if applicable)

| Storage | Key | Value | Lifecycle |
|---------|-----|-------|-----------|
| Local CA dir | `ca.pem`, `ca-key.pem` | Forward proxy root certificate and key | Created on first forward proxy use |
| Trace directory | `trace_*.jsonl` | Captured Codex API records | Appended during run |

---
*Spec for: standalone-proxy-runner*
*Created: 2026-05-20T22:04:03Z*
