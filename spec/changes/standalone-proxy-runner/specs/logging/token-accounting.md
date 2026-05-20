# Thinking Token Accounting

## Requirement

The standalone project must normalize token usage across Anthropic Messages, OpenAI Responses, OpenAI Chat Completions-compatible streams, and Codex WebSocket records. It must separately report reasoning/thinking token counters when numeric fields are exposed, while preserving visible thinking content blocks when providers return them.

## Scenarios

### Scenario: OpenAI nested reasoning tokens

**WHEN** a response usage object contains `output_tokens_details.reasoning_tokens`
**THEN** normalized usage includes `reasoning_tokens`
**AND** the trace summary increments a reasoning token total.

### Scenario: OpenAI top-level reasoning tokens

**WHEN** a response usage object contains top-level `reasoning_tokens`
**THEN** normalized usage preserves that value
**AND** it is included in summary output.

### Scenario: Anthropic thinking content returned

**WHEN** Anthropic streaming emits `thinking_delta` content
**THEN** the reconstructed response body includes a `thinking` content block
**AND** the trace marks visible thinking content as present even if no separate thinking-token count exists.

## Interface

### Props (if UI component)

Not applicable.

### API Contract (if endpoint)

| Surface | Method | Request | Response |
|---------|--------|---------|----------|
| Usage normalizer | function | Provider usage object | Shared usage dict with token aliases |
| Trace writer summary | function | Written records | Aggregate input/output/cache/reasoning counters |
| SSE reassembler | stream accumulation | SSE chunks | Reconstructed response body with content and usage |

## Persistence (if applicable)

| Storage | Key | Value | Lifecycle |
|---------|-----|-------|-----------|
| Trace record | `response.body.usage` | Provider usage plus normalized aliases where safe | Written per record |
| Summary output | `reasoning_tokens` | Aggregate reasoning tokens | Printed and optionally written at shutdown |
| Trace record | `response.body.content[].thinking` | Visible/summarized Anthropic thinking when returned | Written per record |

---
*Spec for: standalone-proxy-runner*
*Created: 2026-05-20T22:04:03Z*
