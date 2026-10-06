# Parameter JSON order evidence

Both screenshots were captured with ego-browser from official claude-tap self-contained viewers generated from a real local Codex reverse-proxy session using the Responses API and MiniMax-M3.1-Flash-Preview. The comparison is captured request turns 2 and 3; turn 1 was retained to preserve viewer numbering.

The local source was exported to `.traces/trace_parameter_order_redacted.jsonl`. For privacy, the evidence projection retains only request `model`, `stream`, and `client_metadata`, removes request headers and response payloads, and consistently pseudonymizes UUIDs, including UUIDs inside the turn-metadata JSON string. Parameter key order and value equality are preserved. The JSONL and generated viewers remain local and are not committed.

- `trace-viewer-before.png`: upstream main's parameter comparison reports one `client_metadata` change solely because object key order differs.
- `trace-viewer-after.png`: the same projected records using this branch report no parameter changes. This screenshot is cropped by ego-browser to the result area to omit the empty lower portion of the modal.

The absent message/system/tool data in these screenshots is a deliberate privacy projection, not a claim that the original requests lacked those fields. This PR changes only parameter comparison and formatting.
