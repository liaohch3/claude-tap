# PR 139 Real Kimi CLI Evidence

Generated on 2026-05-08 from a real interactive `kimi` CLI run through `claude-tap --tap-client kimi`.

This evidence replaces the earlier deterministic fake-upstream screenshots. The fake upstream remains useful for repeatable pytest coverage, but PR evidence screenshots should show real client and real upstream behavior.

## Trace Source

- JSONL: `.traces/regression-kimi-real-interactive-1778229907/2026-05-08/trace_164507.jsonl`
- Viewer: `.traces/regression-kimi-real-interactive-1778229907/2026-05-08/trace_164507.html`
- Client: `kimi, version 1.41.0`
- Capture command shape: `claude-tap --tap-client kimi --tap-output-dir .traces/regression-kimi-real-interactive-1778229907 --tap-no-open --tap-no-update-check -- --yolo`
- Session shape: one continuous interactive Kimi CLI process with five consecutive user prompts in the same conversation.

## Coverage

- Real Kimi CLI process launched by `claude-tap`
- Real upstream Kimi Chat Completions requests captured at `/chat/completions`
- Captured records: 11
- Response statuses: 11 x 200
- Conversation rounds: `Round 1` through `Round 5` in one interactive session
- Tool calls captured from assistant responses: 11 `Shell` calls
- Final assistant responses captured: `KIMI_ROUND_1_DONE`, `KIMI_ROUND_2_DONE`, `KIMI_ROUND_3_DONE`, `KIMI_ROUND_4_DONE`, `KIMI_ROUND_5_DONE`
- Request-history check: no empty assistant `content` messages were found in captured requests.
- Trace redaction check: the validation script confirmed the known DeepSeek key string is not present in this Kimi trace.

## Screenshots

- `kimi-real-multiturn-01-system-overview.png` - first captured request with the real Kimi system prompt visible.
- `kimi-real-multiturn-02-round1-tool-scrolled.png` - scrolled Round 1 tool-call response.
- `kimi-real-multiturn-03-round3-scrolled.png` - middle of the same session with Round 3 selected.
- `kimi-real-multiturn-04-round5-history-scrolled.png` - final turn request history after scrolling through accumulated messages.
- `kimi-real-multiturn-05-final-response-scrolled.png` - final assistant response marker from Round 5.
- `kimi-real-multiturn-06-full-json-scrolled.png` - scrolled Full JSON from the real multi-turn trace.

## Validation

```bash
UV_NO_SYNC=1 uv run python scripts/check_screenshots.py .agents/evidence/pr/pr139
UV_NO_SYNC=1 uv run python scripts/verify_screenshots.py .traces/regression-kimi-real-interactive-1778229907/2026-05-08/trace_164507.html
UV_NO_SYNC=1 uv run python - <<'PY'
import json
from collections import Counter
from pathlib import Path

path = Path(".traces/regression-kimi-real-interactive-1778229907/2026-05-08/trace_164507.jsonl")
records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
text = path.read_text()

tool_calls = Counter()
empty_assistant_messages = 0

for record in records:
    for msg in record["request"]["body"].get("messages", []):
        if msg.get("role") == "assistant" and msg.get("content") in ("", []):
            empty_assistant_messages += 1
    message = record["response"]["body"]["choices"][0]["message"]
    for tool_call in message.get("tool_calls") or []:
        tool_calls[tool_call["function"]["name"]] += 1

assert len(records) == 11
assert all(record["request"]["path"] == "/chat/completions" for record in records)
assert all(record["response"]["status"] == 200 for record in records)
assert tool_calls["Shell"] == 11
assert empty_assistant_messages == 0
for round_id in range(1, 6):
    assert f"KIMI_ROUND_{round_id}_DONE" in text
assert "sk-31b30bd5398e41f9b86f4763b4592cb1" not in text
PY
```
