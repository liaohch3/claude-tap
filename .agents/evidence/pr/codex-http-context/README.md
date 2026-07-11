# Codex HTTP Context Evidence

These screenshots come from real Codex 0.144.1 runs against the ChatGPT Codex
backend. The test prompt used only a controlled `/tmp` workspace. Raw traces
are intentionally not committed.

## Before

`before-dashboard-continuation-warning.png` was captured from `origin/main`.
Codex used its built-in WebSocket provider. The tool-result turn contains a
`previous_response_id` but no user message in that request, so the viewer shows
the stateful continuation warning.

## After

`after-codex-http-full-context.png` was captured after launching Codex with the
temporary `claude-tap-openai` provider and `supports_websockets=false`. The
initial run and resumed turn produced four `POST /v1/responses` records and no
WebSocket records. Every POST contained a user message; tool-result POSTs also
contained the preceding tool call and output, with no `previous_response_id`.

The after screenshot shows the resumed user turn, tool call, and tool result in
the same self-contained request context without a continuation warning.
