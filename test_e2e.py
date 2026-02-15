#!/usr/bin/env python3
"""End-to-end test for claude-tap.

Creates a fake 'claude' script + a fake upstream API server,
then runs `python claude_tap.py` as a real subprocess and
verifies the full pipeline: proxy startup → claude launch → request
forwarding → JSONL recording.
"""

import asyncio
import gzip
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

FAKE_UPSTREAM_PORT = 19199

FAKE_CLAUDE_SCRIPT = r'''#!/usr/bin/env python3
"""Fake claude CLI — sends requests to ANTHROPIC_BASE_URL then exits."""
import json, os, sys, urllib.request

base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
url = f"{base}/v1/messages"

# Turn 1: non-streaming request
req_body = json.dumps({
    "model": "claude-test-model",
    "max_tokens": 100,
    "messages": [{"role": "user", "content": "hello"}],
}).encode()
req = urllib.request.Request(url, data=req_body, headers={
    "Content-Type": "application/json",
    "x-api-key": "sk-ant-test-key-12345678",
    "anthropic-version": "2023-06-01",
})
try:
    with urllib.request.urlopen(req) as resp:
        data = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            import gzip as gz
            data = gz.decompress(data)
        body = json.loads(data)
        print(f"[fake-claude] Turn 1: {body.get('content', [{}])[0].get('text', '?')}")
except Exception as e:
    print(f"[fake-claude] Turn 1 error: {e}", file=sys.stderr)
    sys.exit(1)

# Turn 2: streaming request
req_body2 = json.dumps({
    "model": "claude-test-model",
    "max_tokens": 100,
    "stream": True,
    "messages": [{"role": "user", "content": "count to 3"}],
}).encode()
req2 = urllib.request.Request(url, data=req_body2, headers={
    "Content-Type": "application/json",
    "x-api-key": "sk-ant-test-key-12345678",
    "anthropic-version": "2023-06-01",
})
try:
    with urllib.request.urlopen(req2) as resp:
        chunks = resp.read().decode()
        print(f"[fake-claude] Turn 2: SSE ({len(chunks)} chars)")
except Exception as e:
    print(f"[fake-claude] Turn 2 error: {e}", file=sys.stderr)
    sys.exit(1)

print("[fake-claude] Done.")
'''


def run_fake_upstream_in_thread():
    """Start fake upstream in a background thread with its own event loop."""
    from aiohttp import web

    ready = threading.Event()
    loop = None
    runner = None

    async def handler(request):
        body = await request.read()
        req = json.loads(body) if body else {}

        if req.get("stream"):
            resp = web.StreamResponse(
                status=200,
                headers={"Content-Type": "text/event-stream"},
            )
            await resp.prepare(request)
            events = [
                ("message_start", {"type": "message_start", "message": {
                    "id": "msg_stream_1", "type": "message", "role": "assistant",
                    "content": [], "model": req.get("model", "test"),
                    "usage": {"input_tokens": 20, "output_tokens": 0}
                }}),
                ("content_block_start", {"type": "content_block_start", "index": 0,
                    "content_block": {"type": "text", "text": ""}}),
                ("content_block_delta", {"type": "content_block_delta", "index": 0,
                    "delta": {"type": "text_delta", "text": "1, "}}),
                ("content_block_delta", {"type": "content_block_delta", "index": 0,
                    "delta": {"type": "text_delta", "text": "2, "}}),
                ("content_block_delta", {"type": "content_block_delta", "index": 0,
                    "delta": {"type": "text_delta", "text": "3"}}),
                ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                ("message_delta", {"type": "message_delta",
                    "delta": {"stop_reason": "end_turn"},
                    "usage": {"output_tokens": 8}}),
                ("message_stop", {"type": "message_stop"}),
            ]
            for evt, data in events:
                await resp.write(f"event: {evt}\ndata: {json.dumps(data)}\n\n".encode())
            await resp.write_eof()
            return resp
        else:
            payload = json.dumps({
                "id": "msg_nonstream_1", "type": "message", "role": "assistant",
                "content": [{"type": "text", "text": "Hello!"}],
                "model": req.get("model", "test"),
                "usage": {"input_tokens": 15, "output_tokens": 3},
                "stop_reason": "end_turn",
            }).encode()
            compressed = gzip.compress(payload)
            return web.Response(
                status=200, body=compressed,
                headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
            )

    async def serve():
        nonlocal runner
        app = web.Application()
        app.router.add_route("*", "/{path_info:.*}", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", FAKE_UPSTREAM_PORT)
        await site.start()
        ready.set()
        # Run forever until loop is stopped
        while True:
            await asyncio.sleep(3600)

    def thread_main():
        nonlocal loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(serve())
        except (asyncio.CancelledError, RuntimeError):
            pass
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except RuntimeError:
                pass
            loop.close()

    t = threading.Thread(target=thread_main, daemon=True)
    t.start()
    ready.wait(timeout=5)

    def stop():
        if loop and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=3)

    return stop


def test_e2e():
    stop_upstream = run_fake_upstream_in_thread()
    print(f"[test] Fake upstream on :{FAKE_UPSTREAM_PORT}")

    try:
        _run_test()
    finally:
        stop_upstream()


def _run_test():
    project_dir = Path(__file__).parent
    trace_dir = tempfile.mkdtemp(prefix="claude_tap_test_")

    # Create fake claude
    fake_bin_dir = tempfile.mkdtemp(prefix="fake_bin_")
    fake_claude = Path(fake_bin_dir) / "claude"
    fake_claude.write_text(FAKE_CLAUDE_SCRIPT)
    fake_claude.chmod(fake_claude.stat().st_mode | stat.S_IEXEC)

    env = os.environ.copy()
    env["PATH"] = fake_bin_dir + ":" + env.get("PATH", "")

    print(f"[test] Trace dir: {trace_dir}")
    print("[test] Running: python -m claude_tap ...")

    try:
        proc = subprocess.run(
            [
                sys.executable, "-m", "claude_tap",
                "-o", trace_dir,
                "-t", f"http://127.0.0.1:{FAKE_UPSTREAM_PORT}",
            ],
            cwd=str(project_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        print("[test] TIMEOUT — claude_tap.py did not exit in 30s")
        shutil.rmtree(trace_dir, ignore_errors=True)
        shutil.rmtree(fake_bin_dir, ignore_errors=True)
        sys.exit(1)

    print(f"[test] Exit code: {proc.returncode}")
    if proc.stdout.strip():
        print(f"[test] stdout:\n{proc.stdout.rstrip()}")
    if proc.stderr.strip():
        print(f"[test] stderr:\n{proc.stderr.rstrip()}")

    # ── Assertions ──

    # Trace file exists
    trace_files = list(Path(trace_dir).glob("*.jsonl"))
    assert len(trace_files) == 1, f"Expected 1 trace file, got {trace_files}"
    trace_file = trace_files[0]

    # Log file exists
    log_files = list(Path(trace_dir).glob("*.log"))
    assert len(log_files) == 1, f"Expected 1 log file, got {log_files}"
    log_content = log_files[0].read_text()
    print(f"[test] Proxy log:\n{log_content.rstrip()}")

    # Parse JSONL records
    with open(trace_file) as f:
        records = [json.loads(line) for line in f if line.strip()]

    print(f"[test] Recorded {len(records)} API calls")
    assert len(records) == 2, f"Expected 2 records, got {len(records)}"

    # ── Turn 1: non-streaming (gzip compressed upstream) ──
    r1 = records[0]
    assert r1["turn"] == 1
    assert r1["request"]["method"] == "POST"
    assert "/v1/messages" in r1["request"]["path"]
    assert r1["request"]["body"]["model"] == "claude-test-model"
    assert r1["response"]["status"] == 200
    assert r1["response"]["body"]["content"][0]["text"] == "Hello!"
    # API key redaction (header name may be title-cased)
    hdrs = {k.lower(): v for k, v in r1["request"]["headers"].items()}
    api_key = hdrs.get("x-api-key", "")
    assert api_key.endswith("..."), f"API key not redacted: {api_key}"
    assert "12345678" not in api_key
    print("  ✅ Turn 1 (non-streaming, gzip): OK")

    # ── Turn 2: streaming (SSE) ──
    r2 = records[1]
    assert r2["turn"] == 2
    assert r2["request"]["body"]["stream"] is True
    assert r2["response"]["status"] == 200
    assert r2["response"]["body"]["content"][0]["text"] == "1, 2, 3"
    assert r2["response"]["body"]["usage"]["output_tokens"] == 8
    assert r2["response"]["body"]["stop_reason"] == "end_turn"
    assert "sse_events" in r2["response"]
    assert len(r2["response"]["sse_events"]) == 8
    print("  ✅ Turn 2 (streaming, SSE reassembly): OK")

    # ── Terminal output is clean ──
    assert "Trace summary" in proc.stdout
    assert "Recorded 2 API calls" in proc.stdout
    assert "[Turn" not in proc.stdout, "Proxy logs leaked to stdout!"
    print("  ✅ Terminal output: clean")

    # ── Proxy log has details ──
    assert "[Turn 1]" in log_content
    assert "[Turn 2]" in log_content
    print("  ✅ Proxy log: has Turn details")

    # ── HTML viewer generated ──
    html_files = list(Path(trace_dir).glob("*.html"))
    assert len(html_files) == 1, f"Expected 1 HTML file, got {html_files}"
    html_content = html_files[0].read_text()
    assert "EMBEDDED_TRACE_DATA" in html_content
    assert "claude-test-model" in html_content
    assert "Hello!" in html_content
    assert "View:" in proc.stdout
    print("  ✅ HTML viewer: generated with embedded data")

    print("\n✅ E2E test PASSED")

    # Cleanup
    shutil.rmtree(trace_dir, ignore_errors=True)
    shutil.rmtree(fake_bin_dir, ignore_errors=True)


## ---------------------------------------------------------------------------
## Helper: generic fake upstream starter (reusable across tests)
## ---------------------------------------------------------------------------

def _start_fake_upstream(port, handler_fn):
    """Start a fake upstream server on `port` using `handler_fn` as the aiohttp handler.
    Returns a stop() callable."""
    from aiohttp import web

    ready = threading.Event()
    loop = None
    runner = None

    async def serve():
        nonlocal runner
        app = web.Application()
        app.router.add_route("*", "/{path_info:.*}", handler_fn)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", port)
        await site.start()
        ready.set()
        while True:
            await asyncio.sleep(3600)

    def thread_main():
        nonlocal loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(serve())
        except (asyncio.CancelledError, RuntimeError):
            pass
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except RuntimeError:
                pass
            loop.close()

    t = threading.Thread(target=thread_main, daemon=True)
    t.start()
    ready.wait(timeout=5)

    def stop():
        if loop and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=3)

    return stop


def _run_claude_tap(project_dir, trace_dir, fake_bin_dir, upstream_port, timeout=30):
    """Run claude_tap as a subprocess pointing at `upstream_port`.
    Returns the CompletedProcess."""
    env = os.environ.copy()
    env["PATH"] = fake_bin_dir + ":" + env.get("PATH", "")

    return subprocess.run(
        [
            sys.executable, "-m", "claude_tap",
            "-o", trace_dir,
            "-t", f"http://127.0.0.1:{upstream_port}",
        ],
        cwd=str(project_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _create_fake_claude(script_text):
    """Write `script_text` into a temp dir as an executable 'claude' script.
    Returns the temp dir path (string)."""
    fake_bin_dir = tempfile.mkdtemp(prefix="fake_bin_")
    fake_claude = Path(fake_bin_dir) / "claude"
    fake_claude.write_text(script_text)
    fake_claude.chmod(fake_claude.stat().st_mode | stat.S_IEXEC)
    return fake_bin_dir


## ---------------------------------------------------------------------------
## Test 2: test_upstream_error
## ---------------------------------------------------------------------------

FAKE_UPSTREAM_ERROR_PORT = 19200

FAKE_CLAUDE_ERROR_SCRIPT = r'''#!/usr/bin/env python3
"""Fake claude CLI — sends a request and expects a 500 error."""
import json, os, sys, urllib.request, urllib.error

base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
url = f"{base}/v1/messages"

req_body = json.dumps({
    "model": "claude-test-model",
    "max_tokens": 100,
    "messages": [{"role": "user", "content": "trigger error"}],
}).encode()
req = urllib.request.Request(url, data=req_body, headers={
    "Content-Type": "application/json",
    "x-api-key": "sk-ant-test-key-12345678",
    "anthropic-version": "2023-06-01",
})
try:
    with urllib.request.urlopen(req) as resp:
        print(f"[fake-claude] Unexpected success: {resp.status}", file=sys.stderr)
        sys.exit(1)
except urllib.error.HTTPError as e:
    body = e.read().decode()
    print(f"[fake-claude] Got HTTP {e.code}: {body}")
    # Exit 0 — we expected the error
except Exception as e:
    print(f"[fake-claude] Unexpected error: {e}", file=sys.stderr)
    sys.exit(1)

print("[fake-claude] Done.")
'''


def test_upstream_error():
    """Test that when upstream returns 500, the proxy forwards it correctly
    and records it in the trace."""
    from aiohttp import web

    async def error_handler(request):
        await request.read()
        error_payload = json.dumps({
            "type": "error",
            "error": {"type": "internal_server_error", "message": "Something went wrong"},
        }).encode()
        return web.Response(
            status=500, body=error_payload,
            headers={"Content-Type": "application/json"},
        )

    stop_upstream = _start_fake_upstream(FAKE_UPSTREAM_ERROR_PORT, error_handler)
    print(f"\n[test_upstream_error] Fake upstream on :{FAKE_UPSTREAM_ERROR_PORT}")

    project_dir = Path(__file__).parent
    trace_dir = tempfile.mkdtemp(prefix="claude_tap_test_error_")
    fake_bin_dir = _create_fake_claude(FAKE_CLAUDE_ERROR_SCRIPT)

    try:
        proc = _run_claude_tap(project_dir, trace_dir, fake_bin_dir, FAKE_UPSTREAM_ERROR_PORT)

        print(f"[test_upstream_error] Exit code: {proc.returncode}")
        if proc.stdout.strip():
            print(f"[test_upstream_error] stdout:\n{proc.stdout.rstrip()}")
        if proc.stderr.strip():
            print(f"[test_upstream_error] stderr:\n{proc.stderr.rstrip()}")

        # Trace file exists
        trace_files = list(Path(trace_dir).glob("*.jsonl"))
        assert len(trace_files) == 1, f"Expected 1 trace file, got {trace_files}"
        trace_file = trace_files[0]

        # Parse JSONL records
        with open(trace_file) as f:
            records = [json.loads(line) for line in f if line.strip()]

        print(f"[test_upstream_error] Recorded {len(records)} API calls")
        assert len(records) == 1, f"Expected 1 record, got {len(records)}"

        r = records[0]
        assert r["turn"] == 1
        assert r["response"]["status"] == 500
        assert r["response"]["body"]["type"] == "error"
        assert r["response"]["body"]["error"]["type"] == "internal_server_error"
        assert r["request"]["body"]["messages"][0]["content"] == "trigger error"
        print("  OK: 500 status recorded correctly in trace")

        # The proxy should still produce summary output
        assert "Trace summary" in proc.stdout
        assert "Recorded 1 API calls" in proc.stdout
        print("  OK: proxy summary output present")

        print("\n  test_upstream_error PASSED")

    except subprocess.TimeoutExpired:
        print("[test_upstream_error] TIMEOUT")
        sys.exit(1)
    finally:
        stop_upstream()
        shutil.rmtree(trace_dir, ignore_errors=True)
        shutil.rmtree(fake_bin_dir, ignore_errors=True)


## ---------------------------------------------------------------------------
## Test 3: test_malformed_sse
## ---------------------------------------------------------------------------

FAKE_UPSTREAM_MALFORMED_PORT = 19201

FAKE_CLAUDE_MALFORMED_SCRIPT = r'''#!/usr/bin/env python3
"""Fake claude CLI — sends a streaming request to a server with malformed SSE."""
import json, os, sys, urllib.request

base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
url = f"{base}/v1/messages"

req_body = json.dumps({
    "model": "claude-test-model",
    "max_tokens": 100,
    "stream": True,
    "messages": [{"role": "user", "content": "malformed stream test"}],
}).encode()
req = urllib.request.Request(url, data=req_body, headers={
    "Content-Type": "application/json",
    "x-api-key": "sk-ant-test-key-12345678",
    "anthropic-version": "2023-06-01",
})
try:
    with urllib.request.urlopen(req) as resp:
        chunks = resp.read().decode()
        print(f"[fake-claude] Got SSE response ({len(chunks)} chars)")
except Exception as e:
    print(f"[fake-claude] Error: {e}", file=sys.stderr)
    sys.exit(1)

print("[fake-claude] Done.")
'''


def test_malformed_sse():
    """Test that when the SSE stream is malformed (missing event type, truncated
    data, garbage lines), the proxy handles it gracefully without crashing and
    still records what it can."""
    from aiohttp import web

    async def malformed_sse_handler(request):
        body = await request.read()
        req = json.loads(body) if body else {}

        resp = web.StreamResponse(
            status=200,
            headers={"Content-Type": "text/event-stream"},
        )
        await resp.prepare(request)

        # 1. Valid message_start event
        valid_start = {
            "type": "message_start",
            "message": {
                "id": "msg_malformed_1", "type": "message", "role": "assistant",
                "content": [], "model": req.get("model", "test"),
                "usage": {"input_tokens": 10, "output_tokens": 0},
            },
        }
        await resp.write(f"event: message_start\ndata: {json.dumps(valid_start)}\n\n".encode())

        # 2. Data line without a preceding event: line — should be ignored
        await resp.write(b"data: {\"orphan\": true}\n\n")

        # 3. Event with truncated/invalid JSON
        await resp.write(b"event: content_block_delta\ndata: {\"broken json\n\n")

        # 4. Random garbage line
        await resp.write(b"this is not SSE at all\n\n")

        # 5. Valid content_block_start + delta + stop to produce some text
        await resp.write(
            f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}})}\n\n".encode()
        )
        await resp.write(
            f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': 'partial'}})}\n\n".encode()
        )
        await resp.write(
            f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n".encode()
        )

        # 6. Valid message_delta and message_stop
        await resp.write(
            f"event: message_delta\ndata: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}, 'usage': {'output_tokens': 2}})}\n\n".encode()
        )
        await resp.write(
            f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n".encode()
        )

        await resp.write_eof()
        return resp

    stop_upstream = _start_fake_upstream(FAKE_UPSTREAM_MALFORMED_PORT, malformed_sse_handler)
    print(f"\n[test_malformed_sse] Fake upstream on :{FAKE_UPSTREAM_MALFORMED_PORT}")

    project_dir = Path(__file__).parent
    trace_dir = tempfile.mkdtemp(prefix="claude_tap_test_malformed_")
    fake_bin_dir = _create_fake_claude(FAKE_CLAUDE_MALFORMED_SCRIPT)

    try:
        proc = _run_claude_tap(project_dir, trace_dir, fake_bin_dir, FAKE_UPSTREAM_MALFORMED_PORT)

        print(f"[test_malformed_sse] Exit code: {proc.returncode}")
        if proc.stdout.strip():
            print(f"[test_malformed_sse] stdout:\n{proc.stdout.rstrip()}")
        if proc.stderr.strip():
            print(f"[test_malformed_sse] stderr:\n{proc.stderr.rstrip()}")

        # Proxy should NOT crash (exit code 0 from fake claude)
        assert proc.returncode == 0, f"Expected exit code 0, got {proc.returncode}"
        print("  OK: proxy did not crash")

        # Trace file exists
        trace_files = list(Path(trace_dir).glob("*.jsonl"))
        assert len(trace_files) == 1, f"Expected 1 trace file, got {trace_files}"
        trace_file = trace_files[0]

        with open(trace_file) as f:
            records = [json.loads(line) for line in f if line.strip()]

        assert len(records) == 1, f"Expected 1 record, got {len(records)}"
        r = records[0]
        assert r["turn"] == 1
        assert r["response"]["status"] == 200
        assert r["request"]["body"]["stream"] is True

        # The SSE events list should contain the events the reassembler parsed
        # (both valid and malformed ones that had an event: prefix)
        sse_events = r["response"]["sse_events"]
        assert len(sse_events) >= 5, f"Expected at least 5 SSE events, got {len(sse_events)}"
        print(f"  OK: recorded {len(sse_events)} SSE events (including malformed)")

        # The reconstructed body should still have the partial text from valid events
        body = r["response"]["body"]
        assert body is not None, "Expected reconstructed body, got None"
        assert body["content"][0]["text"] == "partial"
        print("  OK: reconstructed body has 'partial' text from valid events")

        assert "Trace summary" in proc.stdout
        print("  OK: summary present")

        print("\n  test_malformed_sse PASSED")

    except subprocess.TimeoutExpired:
        print("[test_malformed_sse] TIMEOUT")
        sys.exit(1)
    finally:
        stop_upstream()
        shutil.rmtree(trace_dir, ignore_errors=True)
        shutil.rmtree(fake_bin_dir, ignore_errors=True)


## ---------------------------------------------------------------------------
## Test 4: test_large_payload
## ---------------------------------------------------------------------------

FAKE_UPSTREAM_LARGE_PORT = 19202

# The script is generated dynamically to include a 100KB+ system prompt.
# We embed the large payload generation inline in the script.
FAKE_CLAUDE_LARGE_SCRIPT = r'''#!/usr/bin/env python3
"""Fake claude CLI — sends a request with a very large system prompt (100KB+)."""
import json, os, sys, urllib.request

base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
url = f"{base}/v1/messages"

# Generate a large system prompt (over 100KB)
large_system = "You are a helpful assistant. " * 5000  # ~140KB

req_body = json.dumps({
    "model": "claude-test-model",
    "max_tokens": 100,
    "system": large_system,
    "messages": [{"role": "user", "content": "hello"}],
}).encode()
req = urllib.request.Request(url, data=req_body, headers={
    "Content-Type": "application/json",
    "x-api-key": "sk-ant-test-key-12345678",
    "anthropic-version": "2023-06-01",
})
try:
    with urllib.request.urlopen(req) as resp:
        data = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            import gzip as gz
            data = gz.decompress(data)
        body = json.loads(data)
        print(f"[fake-claude] Large payload response: {body.get('content', [{}])[0].get('text', '?')}")
except Exception as e:
    print(f"[fake-claude] Error: {e}", file=sys.stderr)
    sys.exit(1)

print("[fake-claude] Done.")
'''


def test_large_payload():
    """Test with a very large system prompt (100KB+) to ensure the proxy handles
    large request bodies correctly through forwarding and recording."""
    from aiohttp import web

    async def large_handler(request):
        body = await request.read()
        req = json.loads(body) if body else {}

        # Verify we received the large system prompt
        system = req.get("system", "")
        payload = json.dumps({
            "id": "msg_large_1", "type": "message", "role": "assistant",
            "content": [{"type": "text", "text": f"Received system prompt of {len(system)} chars"}],
            "model": req.get("model", "test"),
            "usage": {"input_tokens": 50000, "output_tokens": 10},
            "stop_reason": "end_turn",
        }).encode()
        compressed = gzip.compress(payload)
        return web.Response(
            status=200, body=compressed,
            headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
        )

    stop_upstream = _start_fake_upstream(FAKE_UPSTREAM_LARGE_PORT, large_handler)
    print(f"\n[test_large_payload] Fake upstream on :{FAKE_UPSTREAM_LARGE_PORT}")

    project_dir = Path(__file__).parent
    trace_dir = tempfile.mkdtemp(prefix="claude_tap_test_large_")
    fake_bin_dir = _create_fake_claude(FAKE_CLAUDE_LARGE_SCRIPT)

    try:
        proc = _run_claude_tap(project_dir, trace_dir, fake_bin_dir, FAKE_UPSTREAM_LARGE_PORT)

        print(f"[test_large_payload] Exit code: {proc.returncode}")
        if proc.stdout.strip():
            print(f"[test_large_payload] stdout:\n{proc.stdout.rstrip()}")
        if proc.stderr.strip():
            print(f"[test_large_payload] stderr:\n{proc.stderr.rstrip()}")

        assert proc.returncode == 0, f"Expected exit code 0, got {proc.returncode}"
        print("  OK: proxy handled large payload without crashing")

        # Trace file exists
        trace_files = list(Path(trace_dir).glob("*.jsonl"))
        assert len(trace_files) == 1, f"Expected 1 trace file, got {trace_files}"
        trace_file = trace_files[0]

        with open(trace_file) as f:
            records = [json.loads(line) for line in f if line.strip()]

        assert len(records) == 1, f"Expected 1 record, got {len(records)}"
        r = records[0]

        # Verify the large system prompt was captured in the trace
        system_prompt = r["request"]["body"]["system"]
        assert len(system_prompt) > 100_000, f"System prompt only {len(system_prompt)} chars, expected >100KB"
        print(f"  OK: system prompt recorded ({len(system_prompt)} chars)")

        # Verify response was forwarded and recorded
        assert r["response"]["status"] == 200
        resp_text = r["response"]["body"]["content"][0]["text"]
        assert "Received system prompt of" in resp_text
        # Check the upstream reported the full prompt size
        reported_len = int(resp_text.split("of ")[1].split(" ")[0])
        assert reported_len > 100_000, f"Upstream only received {reported_len} chars"
        print(f"  OK: upstream received full payload ({reported_len} chars)")

        assert "Trace summary" in proc.stdout
        assert "Recorded 1 API calls" in proc.stdout
        print("  OK: summary present")

        # Verify the JSONL trace file is large (should contain the 100KB+ prompt)
        trace_size = trace_file.stat().st_size
        assert trace_size > 100_000, f"Trace file only {trace_size} bytes, expected >100KB"
        print(f"  OK: trace file is {trace_size} bytes (contains full payload)")

        print("\n  test_large_payload PASSED")

    except subprocess.TimeoutExpired:
        print("[test_large_payload] TIMEOUT")
        sys.exit(1)
    finally:
        stop_upstream()
        shutil.rmtree(trace_dir, ignore_errors=True)
        shutil.rmtree(fake_bin_dir, ignore_errors=True)


## ---------------------------------------------------------------------------
## Test 5: test_concurrent_requests
## ---------------------------------------------------------------------------

FAKE_UPSTREAM_CONCURRENT_PORT = 19203

FAKE_CLAUDE_CONCURRENT_SCRIPT = r'''#!/usr/bin/env python3
"""Fake claude CLI — sends multiple requests concurrently using threads."""
import json, os, sys, threading, urllib.request

base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
url = f"{base}/v1/messages"

NUM_THREADS = 5
results = [None] * NUM_THREADS
errors = [None] * NUM_THREADS

def send_request(idx):
    req_body = json.dumps({
        "model": "claude-test-model",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": f"concurrent request {idx}"}],
    }).encode()
    req = urllib.request.Request(url, data=req_body, headers={
        "Content-Type": "application/json",
        "x-api-key": "sk-ant-test-key-12345678",
        "anthropic-version": "2023-06-01",
    })
    try:
        with urllib.request.urlopen(req) as resp:
            data = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                import gzip as gz
                data = gz.decompress(data)
            results[idx] = json.loads(data)
    except Exception as e:
        errors[idx] = str(e)

threads = []
for i in range(NUM_THREADS):
    t = threading.Thread(target=send_request, args=(i,))
    threads.append(t)
    t.start()

for t in threads:
    t.join(timeout=10)

success = sum(1 for r in results if r is not None)
fail = sum(1 for e in errors if e is not None)
print(f"[fake-claude] {success} succeeded, {fail} failed")
for i, e in enumerate(errors):
    if e:
        print(f"[fake-claude] Thread {i} error: {e}", file=sys.stderr)

if fail > 0:
    sys.exit(1)
print("[fake-claude] Done.")
'''


def test_concurrent_requests():
    """Test that multiple simultaneous requests are handled correctly by the
    proxy. Uses threads in the fake claude to send 5 requests at once."""
    from aiohttp import web

    # Use a counter to track requests (thread-safe via asyncio single-threaded loop)
    request_count = {"n": 0}

    async def concurrent_handler(request):
        body = await request.read()
        req = json.loads(body) if body else {}

        request_count["n"] += 1
        n = request_count["n"]

        # Add a small delay to simulate real processing and ensure overlap
        await asyncio.sleep(0.1)

        user_msg = ""
        if isinstance(req.get("messages"), list) and req["messages"]:
            user_msg = req["messages"][0].get("content", "")

        payload = json.dumps({
            "id": f"msg_concurrent_{n}", "type": "message", "role": "assistant",
            "content": [{"type": "text", "text": f"Reply to: {user_msg}"}],
            "model": req.get("model", "test"),
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "stop_reason": "end_turn",
        }).encode()
        compressed = gzip.compress(payload)
        return web.Response(
            status=200, body=compressed,
            headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
        )

    stop_upstream = _start_fake_upstream(FAKE_UPSTREAM_CONCURRENT_PORT, concurrent_handler)
    print(f"\n[test_concurrent_requests] Fake upstream on :{FAKE_UPSTREAM_CONCURRENT_PORT}")

    project_dir = Path(__file__).parent
    trace_dir = tempfile.mkdtemp(prefix="claude_tap_test_concurrent_")
    fake_bin_dir = _create_fake_claude(FAKE_CLAUDE_CONCURRENT_SCRIPT)

    try:
        proc = _run_claude_tap(project_dir, trace_dir, fake_bin_dir, FAKE_UPSTREAM_CONCURRENT_PORT)

        print(f"[test_concurrent_requests] Exit code: {proc.returncode}")
        if proc.stdout.strip():
            print(f"[test_concurrent_requests] stdout:\n{proc.stdout.rstrip()}")
        if proc.stderr.strip():
            print(f"[test_concurrent_requests] stderr:\n{proc.stderr.rstrip()}")

        assert proc.returncode == 0, f"Expected exit code 0, got {proc.returncode}"
        print("  OK: proxy handled concurrent requests without crashing")

        # Trace file exists
        trace_files = list(Path(trace_dir).glob("*.jsonl"))
        assert len(trace_files) == 1, f"Expected 1 trace file, got {trace_files}"
        trace_file = trace_files[0]

        with open(trace_file) as f:
            records = [json.loads(line) for line in f if line.strip()]

        print(f"[test_concurrent_requests] Recorded {len(records)} API calls")
        assert len(records) == 5, f"Expected 5 records, got {len(records)}"

        # All records should have status 200
        for i, r in enumerate(records):
            assert r["response"]["status"] == 200, f"Record {i}: status={r['response']['status']}"

        # Each record should have a unique turn number
        turns = sorted([r["turn"] for r in records])
        assert turns == [1, 2, 3, 4, 5], f"Expected turns [1..5], got {turns}"
        print("  OK: all 5 turns recorded with unique turn numbers")

        # Verify each response echoes back its request content
        for r in records:
            req_content = r["request"]["body"]["messages"][0]["content"]
            resp_text = r["response"]["body"]["content"][0]["text"]
            assert req_content in resp_text, \
                f"Response '{resp_text}' does not contain request content '{req_content}'"
        print("  OK: each response correctly matches its request")

        # All request IDs should be unique
        req_ids = [r["request_id"] for r in records]
        assert len(set(req_ids)) == 5, f"Expected 5 unique request IDs, got {len(set(req_ids))}"
        print("  OK: all request IDs are unique")

        assert "Trace summary" in proc.stdout
        assert "Recorded 5 API calls" in proc.stdout
        print("  OK: summary present")

        print("\n  test_concurrent_requests PASSED")

    except subprocess.TimeoutExpired:
        print("[test_concurrent_requests] TIMEOUT")
        sys.exit(1)
    finally:
        stop_upstream()
        shutil.rmtree(trace_dir, ignore_errors=True)
        shutil.rmtree(fake_bin_dir, ignore_errors=True)


## ---------------------------------------------------------------------------
## Run all tests
## ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_e2e()
    test_upstream_error()
    test_malformed_sse()
    test_large_payload()
    test_concurrent_requests()
    print("\n" + "=" * 60)
    print("  ALL E2E TESTS PASSED")
    print("=" * 60)
