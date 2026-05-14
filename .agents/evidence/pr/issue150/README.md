# Issue 150 Evidence

Source trace: `.traces/issue150-codex-tool-order/2026-05-11/2026-05-11/trace_041105.jsonl`

The screenshot was captured from a real Codex WebSocket run through claude-tap. The run produced two separate `exec_command` calls and two matching tool results in one WebSocket session. Local repository paths were redacted in the screenshot as `[repo]`; system/user context blocks were hidden to keep the evidence focused on the Messages ordering and avoid exposing local context.

Evidence:

- `codex-ws-tool-result-order.png` shows the final derived viewer entry rendering `ASSISTANT tool_use -> TOOL result -> ASSISTANT tool_use -> TOOL result`.
