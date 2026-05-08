# PR 139 Real Kimi CLI Evidence

Generated on 2026-05-08 from a real `kimi` CLI run through `claude-tap --tap-client kimi`.

This evidence replaces the earlier deterministic fake-upstream screenshots. The fake upstream remains useful for repeatable pytest coverage, but PR evidence screenshots should show real client and real upstream behavior.

## Trace Source

- JSONL: `.traces/regression-kimi-real-tools-1778229537/2026-05-08/trace_163857.jsonl`
- Viewer: `.traces/regression-kimi-real-tools-1778229537/2026-05-08/trace_163857.html`
- Client: `kimi, version 1.41.0`
- Capture command shape: `claude-tap --tap-client kimi -- ... kimi --print --final-message-only --yolo -p <prompt>`

## Coverage

- Real Kimi CLI process launched by `claude-tap`
- Real upstream Kimi Chat Completions requests captured at `/chat/completions`
- Captured records: 3
- Response statuses: 200, 200, 200
- Tool calls captured: `Shell`, `Shell`, `Shell`
- Final assistant response captured: `KIMI_REAL_TOOL_OK`
- Trace redaction check: the validation script confirmed the known DeepSeek key string is not present in this Kimi trace.

## Screenshots

- `kimi-real-01-overview.png` - real Kimi trace overview.
- `kimi-real-02-system-and-tools.png` - real request context with system/tool sections.
- `kimi-real-03-shell-tool-followup.png` - second real tool-call turn.
- `kimi-real-04-final-response.png` - final real assistant response.
- `kimi-real-05-full-json-scrolled.png` - scrolled Full JSON from the real trace.

## Validation

```bash
UV_NO_SYNC=1 uv run python scripts/check_screenshots.py .agents/evidence/pr/pr139
UV_NO_SYNC=1 uv run python scripts/verify_screenshots.py .traces/regression-kimi-real-tools-1778229537/2026-05-08/trace_163857.html
UV_NO_SYNC=1 uv run python - <<'PY'
import json
from pathlib import Path

path = Path(".traces/regression-kimi-real-tools-1778229537/2026-05-08/trace_163857.jsonl")
records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
assert len(records) == 3
assert all(record["request"]["path"] == "/chat/completions" for record in records)
assert all(record["response"]["status"] == 200 for record in records)
assert "KIMI_REAL_TOOL_OK" in path.read_text()
PY
```
