# TTFT (time-to-first-token) trace evidence

## Problem shape

Streaming trace records carried `timestamp` and `duration_ms` (completion wall
clock / total request time) but not when the first upstream byte arrived, so
consumers could not split time-to-first-token from generation time.

## What the feature does

`proxy_handler` (reverse proxy) and `ForwardProxyServer._handle_streaming`
(TLS MITM forward proxy) now stamp `record["ttft_ms"]` on streaming records:
the wall-clock delta between sending the upstream request and receiving the
first upstream byte. The reference clock starts immediately before the
upstream request is sent, and the first upstream chunk is prefetched before
any downstream write, so the value measures upstream latency alone.
Non-streaming records omit the key.

## How the evidence was produced

The screenshots are captured from a real proxy run, not hand-seeded records:

1. A local SSE upstream is served over real TCP (plain `aiohttp` app,
   ~80 ms first-token latency, then dripped)
2. The real `proxy_handler` aiohttp application under review forwards two
   streaming `POST /v1/messages` requests to it
3. The resulting records flow through `_build_record` -> `TraceWriter` ->
   `TraceStore` into `.traces/ttft/traces.sqlite3`
4. The script asserts every streaming record carries `ttft_ms` within
   `[0, duration_ms]` before capturing the dashboard from that same store

The captured run measured `ttft_ms=81` / `duration_ms=184` on both turns.

## Deterministic recreation

From the repo root:

```bash
uv run python .agents/evidence/pr/ttft/run_proxy_and_capture.py
uv run python scripts/check_screenshots.py .agents/evidence/pr/ttft/
```

`.traces/` remains gitignored; reviewers reproduce the store with the
committed script above.

## Artifacts

- `run_proxy_and_capture.py`: real reverse-proxy run + Playwright capture
- `dashboard-ttft-session.png`: session detail with the two streamed turns
- `dashboard-ttft-record-json.png`: Turn 1 raw JSON showing `"ttft_ms": 81`
  on the record
