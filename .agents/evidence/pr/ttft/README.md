# TTFT (time-to-first-token) trace evidence

## Problem shape

Streaming trace records carried `timestamp` and `duration_ms` (completion wall
clock / total request time) but not when the first upstream byte arrived, so
consumers could not split time-to-first-token from generation time.

## What the feature does

`proxy_handler` (reverse proxy) and `ForwardProxyServer._handle_streaming`
(TLS MITM forward proxy) now stamp `record["ttft_ms"]` on streaming records:
the wall-clock delta between sending the upstream request and receiving the
first upstream byte. Non-streaming records omit the key.

## Deterministic recreation

From the repo root:

```bash
uv run python .agents/evidence/pr/ttft/seed_and_capture.py
uv run python scripts/check_screenshots.py .agents/evidence/pr/ttft/
```

The seed script:

1. Builds a local store at `.traces/ttft/traces.sqlite3` through
   `TraceStore.create_session()` / `append_record()` / `finalize_session()`
   using the exact record shape `_build_record` emits for streaming responses
2. Seeds two streaming `/v1/messages` turns with `ttft_ms` 412 and 297, both
   smaller than their `duration_ms`
3. Asserts `ttft_ms` survives the TraceStore round-trip
4. Opens `LiveViewerServer(dashboard_mode=True)`, selects the session, and
   writes both screenshots (second one expands Turn 1's raw JSON panel)

`.traces/` remains gitignored; reviewers reproduce the store with the committed
`seed_and_capture.py` script above.

## Artifacts

- `seed_and_capture.py`: deterministic seed + Playwright capture
- `dashboard-ttft-session.png`: session detail with two streaming turns
- `dashboard-ttft-record-json.png`: Turn 1 raw JSON showing `"ttft_ms": 412`
  on the record
