# Viewer Quality Framework Evidence

This evidence belongs to the viewer stability PR. The screenshots were generated
from deterministic contract traces after Playwright assertions confirmed that
the rendered viewer contained semantic sections, not only `Full JSON`.

## Quality Gate

Each screenshot was captured only after the browser test confirmed:

- No `pageerror` or `console.error` was emitted.
- `Full JSON` existed as a fallback section.
- At least one semantic section existed before the fallback.
- Required semantic sections were present for the trace shape.
- Required tools and detail text were visible in the rendered DOM.

## Screenshots

- `anthropic_messages.png` — Anthropic Messages contract with tools, system prompt,
  message history, tool result, response, and token usage.
- `codex_websocket.png` — Codex WebSocket contract with request context, reconstructed
  response output, stream events, and token usage.
- `gemini.png` — Gemini contract with `systemInstruction`, `contents`,
  `functionDeclarations`, `functionCall`, `functionResponse`, SSE output, and
  Gemini usage metadata.
