#!/usr/bin/env python3
"""End-to-end test for claude-tap.

Creates a fake 'claude' script + a fake upstream API server,
then runs `python claude_tap.py` as a real subprocess and
verifies the full pipeline: proxy startup → claude launch → request
forwarding → JSONL recording.
"""

import asyncio
import gzip
import ipaddress
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest
from yarl import URL

from claude_tap.trace import TraceWriter
from tests.conftest import e2e_env, read_proxy_log, read_trace_records


def _writer_for_dir(tmpdir: Path):
    from claude_tap.trace_store import TraceStore

    store = TraceStore(tmpdir / "forward.sqlite3")
    session_id = store.create_session()
    return store, session_id, TraceWriter(session_id, store=store)


FAKE_UPSTREAM_PORT = 19199
PROJECT_ROOT = Path(__file__).resolve().parents[1]

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
    "system": "streaming system prompt must remain stored when raw stream events are omitted",
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
    """Start fake upstream in a background thread with its own event loop.

    Returns (stop_fn, actual_port) where actual_port is the OS-assigned port.
    """
    from aiohttp import web

    ready = threading.Event()
    loop = None
    runner = None
    actual_port_holder: list[int] = []

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
                (
                    "message_start",
                    {
                        "type": "message_start",
                        "message": {
                            "id": "msg_stream_1",
                            "type": "message",
                            "role": "assistant",
                            "content": [],
                            "model": req.get("model", "test"),
                            "usage": {"input_tokens": 20, "output_tokens": 0},
                        },
                    },
                ),
                (
                    "content_block_start",
                    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                ),
                (
                    "content_block_delta",
                    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "1, "}},
                ),
                (
                    "content_block_delta",
                    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "2, "}},
                ),
                (
                    "content_block_delta",
                    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "3"}},
                ),
                ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                (
                    "message_delta",
                    {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 8}},
                ),
                ("message_stop", {"type": "message_stop"}),
            ]
            for evt, data in events:
                await resp.write(f"event: {evt}\ndata: {json.dumps(data)}\n\n".encode())
            await resp.write_eof()
            return resp
        else:
            payload = json.dumps(
                {
                    "id": "msg_nonstream_1",
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Hello!"}],
                    "model": req.get("model", "test"),
                    "usage": {"input_tokens": 15, "output_tokens": 3},
                    "stop_reason": "end_turn",
                }
            ).encode()
            compressed = gzip.compress(payload)
            return web.Response(
                status=200,
                body=compressed,
                headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
            )

    async def serve():
        nonlocal runner
        app = web.Application()
        app.router.add_route("*", "/{path_info:.*}", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)  # OS-assigned port
        await site.start()
        actual_port_holder.append(site._server.sockets[0].getsockname()[1])
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
                if runner:
                    loop.run_until_complete(runner.cleanup())
                loop.run_until_complete(loop.shutdown_asyncgens())
            except RuntimeError:
                pass
            loop.close()

    t = threading.Thread(target=thread_main, daemon=True)
    t.start()
    ready.wait(timeout=5)

    def stop():
        if loop and loop.is_running():
            # Clean up runner first to release the port
            import concurrent.futures

            if runner:
                fut = asyncio.run_coroutine_threadsafe(runner.cleanup(), loop)
                try:
                    fut.result(timeout=3)
                except (concurrent.futures.TimeoutError, RuntimeError):
                    pass
            loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=3)

    return stop, actual_port_holder[0] if actual_port_holder else FAKE_UPSTREAM_PORT


def test_e2e():
    stop_upstream, upstream_port = run_fake_upstream_in_thread()
    print(f"[test] Fake upstream on :{upstream_port}")

    try:
        _run_test(upstream_port)
    finally:
        stop_upstream()


def test_e2e_store_stream_events_flag():
    stop_upstream, upstream_port = run_fake_upstream_in_thread()
    print(f"[test] Fake upstream on :{upstream_port}")

    try:
        _run_test(upstream_port, store_stream_events=True)
    finally:
        stop_upstream()


def _run_test(upstream_port, store_stream_events=False):
    project_dir = PROJECT_ROOT
    trace_dir = tempfile.mkdtemp(prefix="claude_tap_test_")

    # Create fake claude
    fake_bin_dir = tempfile.mkdtemp(prefix="fake_bin_")
    fake_claude = Path(fake_bin_dir) / "claude"
    fake_claude.write_text(FAKE_CLAUDE_SCRIPT)
    fake_claude.chmod(fake_claude.stat().st_mode | stat.S_IEXEC)

    env = os.environ.copy()
    env["PATH"] = fake_bin_dir + ":" + env.get("PATH", "")

    env = e2e_env(env, trace_dir)
    print(f"[test] Trace dir: {trace_dir}")
    print("[test] Running: python -m claude_tap ...")

    try:
        cmd = [
            sys.executable,
            "-m",
            "claude_tap",
            "--tap-output-dir",
            trace_dir,
            "--tap-no-open",
            "--tap-target",
            f"http://127.0.0.1:{upstream_port}",
        ]
        if store_stream_events:
            cmd.append("--tap-store-stream-events")
        proc = subprocess.run(
            cmd,
            cwd=str(project_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        print("[test] TIMEOUT — claude_tap.py did not exit in 30s")
        _cleanup(trace_dir, fake_bin_dir, "e2e")
        sys.exit(1)

    print(f"[test] Exit code: {proc.returncode}")
    if proc.stdout.strip():
        print(f"[test] stdout:\n{proc.stdout.rstrip()}")
    if proc.stderr.strip():
        print(f"[test] stderr:\n{proc.stderr.rstrip()}")

    # ── Assertions ──

    records = read_trace_records(trace_dir)
    assert len(records) == 2, f"Expected 2 records, got {len(records)}"

    log_content = read_proxy_log(trace_dir)
    assert log_content.strip(), "Expected proxy log lines in SQLite"
    print(f"[test] Proxy log:\n{log_content.rstrip()}")

    print(f"[test] Recorded {len(records)} API calls")

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
    assert r2["request"]["body"]["system"] == (
        "streaming system prompt must remain stored when raw stream events are omitted"
    )
    assert r2["request"]["body"]["messages"][0]["content"] == "count to 3"
    assert r2["response"]["status"] == 200
    assert r2["response"]["body"]["content"][0]["text"] == "1, 2, 3"
    assert r2["response"]["body"]["usage"]["output_tokens"] == 8
    assert r2["response"]["body"]["stop_reason"] == "end_turn"
    if store_stream_events:
        assert "sse_events" in r2["response"]
        assert len(r2["response"]["sse_events"]) == 8
        print("  ✅ Turn 2 (streaming, opt-in raw SSE event storage): OK")
    else:
        assert "sse_events" not in r2["response"]
        print("  ✅ Turn 2 (streaming, SSE reassembly without raw event storage): OK")

    # ── Terminal output is clean ──
    assert "Trace summary" in proc.stdout
    assert "API calls: 2" in proc.stdout
    assert "[Turn" not in proc.stdout, "Proxy logs leaked to stdout!"
    print("  ✅ Terminal output: clean")

    # ── Proxy log has details ──
    assert "[Turn 1]" in log_content
    assert "[Turn 2]" in log_content
    print("  ✅ Proxy log: has Turn details")
    assert "Session:" in proc.stdout or "Trace session:" in proc.stdout
    print("  ✅ SQLite session persisted")

    print("\n✅ E2E test PASSED")

    _cleanup(trace_dir, fake_bin_dir, "e2e")


## ---------------------------------------------------------------------------
## Helper: cleanup (--keep aware)
## ---------------------------------------------------------------------------

KEEP_DIR = None  # set by __main__ when --keep is passed


def _cleanup(trace_dir, fake_bin_dir, test_name="test"):
    """Clean up temp dirs. When KEEP_DIR is set, copy trace output there first."""
    if KEEP_DIR:
        for f in Path(trace_dir).iterdir():
            dest = KEEP_DIR / f"{test_name}_{f.name}"
            shutil.copy2(f, dest)
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


def _run_claude_tap(project_dir, trace_dir, fake_bin_dir, upstream_port, timeout=30, tap_client="claude"):
    """Run claude_tap as a subprocess pointing at `upstream_port`.
    Returns the CompletedProcess."""
    env = os.environ.copy()
    env["PATH"] = fake_bin_dir + ":" + env.get("PATH", "")

    env = e2e_env(env, trace_dir)
    cmd = [
        sys.executable,
        "-m",
        "claude_tap",
        "--tap-output-dir",
        trace_dir,
        "--tap-target",
        f"http://127.0.0.1:{upstream_port}",
    ]
    if tap_client != "claude":
        cmd.extend(["--tap-client", tap_client])

    return subprocess.run(
        cmd,
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
        error_payload = json.dumps(
            {
                "type": "error",
                "error": {"type": "internal_server_error", "message": "Something went wrong"},
            }
        ).encode()
        return web.Response(
            status=500,
            body=error_payload,
            headers={"Content-Type": "application/json"},
        )

    stop_upstream = _start_fake_upstream(FAKE_UPSTREAM_ERROR_PORT, error_handler)
    print(f"\n[test_upstream_error] Fake upstream on :{FAKE_UPSTREAM_ERROR_PORT}")

    project_dir = PROJECT_ROOT
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
        records = read_trace_records(trace_dir)
        assert len(records) >= 1, f"Expected trace records in SQLite, got {records}"

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
        assert "API calls: 1" in proc.stdout
        print("  OK: proxy summary output present")

        print("\n  test_upstream_error PASSED")

    except subprocess.TimeoutExpired:
        print("[test_upstream_error] TIMEOUT")
        sys.exit(1)
    finally:
        stop_upstream()
        _cleanup(trace_dir, fake_bin_dir, "upstream_error")


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
                "id": "msg_malformed_1",
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": req.get("model", "test"),
                "usage": {"input_tokens": 10, "output_tokens": 0},
            },
        }
        await resp.write(f"event: message_start\ndata: {json.dumps(valid_start)}\n\n".encode())

        # 2. Data line without a preceding event: line — should be ignored
        await resp.write(b'data: {"orphan": true}\n\n')

        # 3. Event with truncated/invalid JSON
        await resp.write(b'event: content_block_delta\ndata: {"broken json\n\n')

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
        await resp.write(f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n".encode())

        await resp.write_eof()
        return resp

    stop_upstream = _start_fake_upstream(FAKE_UPSTREAM_MALFORMED_PORT, malformed_sse_handler)
    print(f"\n[test_malformed_sse] Fake upstream on :{FAKE_UPSTREAM_MALFORMED_PORT}")

    project_dir = PROJECT_ROOT
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
        records = read_trace_records(trace_dir)
        assert len(records) >= 1, f"Expected trace records in SQLite, got {records}"

        assert len(records) == 1, f"Expected 1 record, got {len(records)}"
        r = records[0]
        assert r["turn"] == 1
        assert r["response"]["status"] == 200
        assert r["request"]["body"]["stream"] is True

        # Raw SSE events are not persisted by default, but the reconstructed
        # body should still be usable.
        assert "sse_events" not in r["response"]
        print("  OK: raw SSE events omitted by default")

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
        _cleanup(trace_dir, fake_bin_dir, "malformed_sse")


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
        payload = json.dumps(
            {
                "id": "msg_large_1",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": f"Received system prompt of {len(system)} chars"}],
                "model": req.get("model", "test"),
                "usage": {"input_tokens": 50000, "output_tokens": 10},
                "stop_reason": "end_turn",
            }
        ).encode()
        compressed = gzip.compress(payload)
        return web.Response(
            status=200,
            body=compressed,
            headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
        )

    stop_upstream = _start_fake_upstream(FAKE_UPSTREAM_LARGE_PORT, large_handler)
    print(f"\n[test_large_payload] Fake upstream on :{FAKE_UPSTREAM_LARGE_PORT}")

    project_dir = PROJECT_ROOT
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
        records = read_trace_records(trace_dir)
        assert len(records) >= 1, f"Expected trace records in SQLite, got {records}"

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
        assert "API calls: 1" in proc.stdout
        print("  OK: summary present")

        payload_size = sum(len(json.dumps(record)) for record in records)
        assert payload_size > 100_000, f"Trace payload only {payload_size} bytes, expected >100KB"
        print(f"  OK: trace payload is {payload_size} bytes (contains full payload)")

        print("\n  test_large_payload PASSED")

    except subprocess.TimeoutExpired:
        print("[test_large_payload] TIMEOUT")
        sys.exit(1)
    finally:
        stop_upstream()
        _cleanup(trace_dir, fake_bin_dir, "large_payload")


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

        payload = json.dumps(
            {
                "id": f"msg_concurrent_{n}",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": f"Reply to: {user_msg}"}],
                "model": req.get("model", "test"),
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "stop_reason": "end_turn",
            }
        ).encode()
        compressed = gzip.compress(payload)
        return web.Response(
            status=200,
            body=compressed,
            headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
        )

    stop_upstream = _start_fake_upstream(FAKE_UPSTREAM_CONCURRENT_PORT, concurrent_handler)
    print(f"\n[test_concurrent_requests] Fake upstream on :{FAKE_UPSTREAM_CONCURRENT_PORT}")

    project_dir = PROJECT_ROOT
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
        records = read_trace_records(trace_dir)
        assert len(records) >= 1, f"Expected trace records in SQLite, got {records}"

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
            assert req_content in resp_text, f"Response '{resp_text}' does not contain request content '{req_content}'"
        print("  OK: each response correctly matches its request")

        # All request IDs should be unique
        req_ids = [r["request_id"] for r in records]
        assert len(set(req_ids)) == 5, f"Expected 5 unique request IDs, got {len(set(req_ids))}"
        print("  OK: all request IDs are unique")

        assert "Trace summary" in proc.stdout
        assert "API calls: 5" in proc.stdout
        print("  OK: summary present")

        print("\n  test_concurrent_requests PASSED")

    except subprocess.TimeoutExpired:
        print("[test_concurrent_requests] TIMEOUT")
        sys.exit(1)
    finally:
        stop_upstream()
        _cleanup(trace_dir, fake_bin_dir, "concurrent")


## ---------------------------------------------------------------------------
## --preview: regenerate HTML from real .traces files and open
## ---------------------------------------------------------------------------


def _cmd_preview():
    """Regenerate HTML viewer from existing .traces data using current viewer.html.

    Usage:
        uv run python test_e2e.py --preview            # latest trace
        uv run python test_e2e.py --preview all         # all traces
        uv run python test_e2e.py --preview 002300      # match by partial name
    """
    import subprocess as sp

    from claude_tap import _generate_html_viewer

    traces_dir = Path(__file__).parent / ".traces"
    if not traces_dir.exists():
        print(f"Error: {traces_dir} does not exist")
        sys.exit(1)

    target = sys.argv[2] if len(sys.argv) > 2 else "latest"
    if target == "all":
        jsonl_files = sorted(traces_dir.glob("*.jsonl"))
    elif target == "latest":
        jsonl_files = sorted(traces_dir.glob("*.jsonl"))[-1:]
    else:
        jsonl_files = [f for f in traces_dir.glob("*.jsonl") if target in f.name]

    if not jsonl_files:
        print(f"No matching .jsonl in {traces_dir}")
        sys.exit(1)

    for jf in jsonl_files:
        html = jf.with_suffix(".html")
        _generate_html_viewer(jf, html)
        print(f"Generated: {html}")

    sp.run(["open", str(jsonl_files[-1].with_suffix(".html"))])


## ---------------------------------------------------------------------------
## --dev: auto multi-turn via claude -p, then open HTML
## ---------------------------------------------------------------------------


def _cmd_dev():
    """Start claude-tap proxy, run multi-turn prompts non-interactively, open HTML.

    Usage:
        uv run python test_e2e.py --dev                          # default prompts
        uv run python test_e2e.py --dev "prompt1" "prompt2" ...  # custom prompts
    """
    import signal
    import subprocess as sp

    project_dir = PROJECT_ROOT
    traces_dir = project_dir / ".traces"
    traces_dir.mkdir(exist_ok=True)

    # Collect prompts: custom or default
    prompts = [a for a in sys.argv[2:] if not a.startswith("-")]
    if not prompts:
        prompts = [
            "Search the web for the latest Claude model release date and summarize in 2 sentences",
            "Now search for how it compares to GPT-5.2 and give a short comparison table",
        ]

    # Start proxy in background via --no-launch
    # -u: unbuffered stdout so we can read the port line immediately
    print("Starting claude-tap proxy...")
    proxy_env = os.environ.copy()
    proxy_env["PYTHONUNBUFFERED"] = "1"
    proxy_proc = sp.Popen(
        [sys.executable, "-u", "-m", "claude_tap", "--tap-output-dir", str(traces_dir), "--tap-no-launch"],
        cwd=str(project_dir),
        env=proxy_env,
        stdout=sp.PIPE,
        stderr=sp.STDOUT,
        text=True,
    )

    # Read proxy output to get the port
    port = None
    for line in proxy_proc.stdout:
        print(line, end="")
        if "listening on" in line:
            port = int(line.strip().rsplit(":", 1)[1])
            break

    if port is None:
        print("Error: could not determine proxy port")
        proxy_proc.terminate()
        sys.exit(1)

    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{port}"
    # Remove vars that make claude think it's inside a nested session
    for k in ["CLAUDECODE", "CLAUDE_CODE_SSE_PORT"]:
        env.pop(k, None)

    try:
        for i, prompt in enumerate(prompts):
            turn = i + 1
            print(f"\n{'=' * 50}")
            print(f"Turn {turn}: {prompt[:70]}{'...' if len(prompt) > 70 else ''}")
            print("=" * 50)

            cmd = ["claude", "-p", prompt]
            if i > 0:
                cmd.insert(2, "-c")  # --continue: resume last conversation

            result = sp.run(cmd, env=env, capture_output=True, text=True, timeout=180)
            if result.stdout:
                lines = result.stdout.strip().split("\n")
                preview = "\n".join(lines[:10])
                if len(lines) > 10:
                    preview += f"\n... ({len(lines) - 10} more lines)"
                print(preview)
            if result.returncode != 0 and result.stderr:
                print(f"stderr: {result.stderr[:200]}")
    except Exception as e:
        print(f"\nError during prompts: {e}")
    finally:
        # Stop proxy
        proxy_proc.send_signal(signal.SIGINT)
        remaining = proxy_proc.stdout.read()
        print(remaining, end="")
        proxy_proc.wait(timeout=10)

    # Find and open the latest HTML
    html_files = sorted(traces_dir.glob("*.html"))
    if html_files:
        latest = html_files[-1]
        print(f"\nOpening: {latest}")
        sp.run(["open", str(latest)])
    else:
        print("\nNo HTML generated")


## ---------------------------------------------------------------------------
## Test 6: test_parse_args — argument passthrough with --tap-* prefix
## ---------------------------------------------------------------------------


def test_parse_args(monkeypatch, tmp_path):
    """Test that --tap-* flags are consumed by claude-tap and everything else
    is forwarded to claude via claude_args."""
    from claude_tap import parse_args

    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
    monkeypatch.chdir(tmp_path)

    # Basic: no args
    a = parse_args([])
    assert a.claude_args == []
    assert a.port == 0
    assert a.output_dir == "./.traces"
    assert a.client == "claude"
    assert a.target == "https://api.anthropic.com"
    assert a.no_launch is False
    assert a.live_viewer is True
    assert a.open_viewer is True
    assert a.client_cmd is None
    assert a.store_stream_events is False
    print("  OK: defaults")

    # Codex defaults
    a = parse_args(["--tap-client", "codex"])
    assert a.client == "codex"
    assert a.target == "https://api.openai.com"
    assert a.claude_args == []
    print("  OK: codex defaults")

    # Claude flags pass through
    a = parse_args(["-c"])
    assert a.claude_args == ["-c"]
    print("  OK: -c forwarded")

    a = parse_args(["--model", "opus", "-c"])
    assert a.claude_args == ["--model", "opus", "-c"]
    print("  OK: --model opus -c forwarded")

    # -p (claude's --print) should NOT be consumed by tap
    a = parse_args(["-p"])
    assert a.claude_args == ["-p"]
    assert a.port == 0
    print("  OK: -p forwarded (no conflict with old --port)")

    # Tap-specific flags consumed
    a = parse_args(["--tap-port", "8080", "--tap-output-dir", "/tmp/t", "--tap-no-open", "--tap-target", "http://x"])
    assert a.port == 8080
    assert a.output_dir == "/tmp/t"
    assert a.target == "http://x"
    assert a.open_viewer is False
    assert a.claude_args == []
    print("  OK: --tap-* flags consumed")

    # VSCode claudeProcessWrapper passes the bundled Claude binary as argv[0].
    wrapped_claude = tmp_path / "claude"
    wrapped_claude.write_text("#!/bin/sh\n", encoding="utf-8")
    a = parse_args([str(wrapped_claude), "--output-format", "stream-json", "--verbose"])
    assert a.client_cmd == str(wrapped_claude)
    assert a.claude_args == ["--output-format", "stream-json", "--verbose"]
    print("  OK: VSCode wrapper Claude binary path consumed")

    prompt_dir_named_claude = tmp_path / "context" / "claude"
    prompt_dir_named_claude.mkdir(parents=True)
    a = parse_args([str(prompt_dir_named_claude), "--output-format", "stream-json"])
    assert a.client_cmd is None
    assert a.claude_args == [str(prompt_dir_named_claude), "--output-format", "stream-json"]
    print("  OK: directory named claude is not consumed as wrapper binary")

    a = parse_args(["--tap-no-live"])
    assert a.live_viewer is False
    assert a.claude_args == []
    print("  OK: --tap-no-live disables live viewer")

    a = parse_args(["--tap-store-stream-events"])
    assert a.store_stream_events is True
    assert a.claude_args == []
    print("  OK: --tap-store-stream-events enables raw event storage")

    # Mix: tap flags + claude flags
    a = parse_args(["--tap-port", "9999", "-c", "--model", "sonnet"])
    assert a.port == 9999
    assert a.claude_args == ["-c", "--model", "sonnet"]
    print("  OK: mixed tap + claude flags")

    # --tap-allow-path
    a = parse_args(["--tap-allow-path", "/custom/api"])
    assert a.extra_allowed_paths == ["/custom/api"]
    print("  OK: --tap-allow-path single")

    a = parse_args(["--tap-allow-path", "/custom/api", "--tap-allow-path", "/another/path"])
    assert a.extra_allowed_paths == ["/custom/api", "/another/path"]
    print("  OK: --tap-allow-path multiple")

    # Complex claude flags
    a = parse_args(["--tap-port", "0", "-p", "--model", "opus", "--system-prompt", "be brief", "-d"])
    assert a.port == 0
    assert a.claude_args == ["-p", "--model", "opus", "--system-prompt", "be brief", "-d"]
    print("  OK: complex claude flags forwarded")

    print("\n  test_parse_args PASSED")


@pytest.mark.asyncio
async def test_async_main_live_viewer_default_opens_when_allowed(monkeypatch, tmp_path):
    """Default live viewer starts, and --tap-no-open controls browser opening."""
    from claude_tap import async_main, parse_args
    from claude_tap.live import LiveViewerServer

    opened_urls = []
    spawned_servers: list[LiveViewerServer] = []

    async def fake_run_client(*args, **kwargs):
        return 0

    async def fake_ensure_shared_dashboard(*, host, port, output_dir, open_browser, open_browser_fn):
        server = LiveViewerServer(port=0, migrate_from=output_dir, dashboard_mode=True)
        await server.start()
        spawned_servers.append(server)
        if open_browser:
            open_browser_fn(server.url)
        return server.url, True

    migration_calls = []
    monkeypatch.setenv("CLOUDTAP_DB", str(tmp_path / "async-main.sqlite3"))
    monkeypatch.setattr("claude_tap.cli.run_client", fake_run_client)
    monkeypatch.setattr("claude_tap.cli._open_browser", opened_urls.append)
    monkeypatch.setattr("claude_tap.cli.ensure_shared_dashboard", fake_ensure_shared_dashboard)
    monkeypatch.setattr("claude_tap.cli.migrate_legacy_traces", migration_calls.append)

    args = parse_args(["--tap-output-dir", str(tmp_path), "--tap-no-update-check"])
    try:
        code = await async_main(args)
    finally:
        for server in spawned_servers:
            await server.stop()

    assert code == 0
    assert len(opened_urls) == 1
    assert all(url.startswith("http://127.0.0.1:") for url in opened_urls)
    assert migration_calls == []


@pytest.mark.asyncio
async def test_async_main_reuses_existing_dashboard_without_reopening_browser(monkeypatch, tmp_path):
    """A second claude-tap run should attach to an existing dashboard without opening another tab."""
    from claude_tap import async_main, parse_args
    from claude_tap.live import LiveViewerServer

    opened_urls = []
    spawned_servers: list[LiveViewerServer] = []
    attach_calls = {"count": 0}

    async def fake_run_client(*args, **kwargs):
        return 0

    async def fake_ensure_shared_dashboard(*, host, port, output_dir, open_browser, open_browser_fn):
        attach_calls["count"] += 1
        if attach_calls["count"] == 1:
            server = LiveViewerServer(port=0, migrate_from=output_dir, dashboard_mode=True)
            await server.start()
            spawned_servers.append(server)
            if open_browser:
                open_browser_fn(server.url)
            return server.url, True
        return f"http://{host}:{port}", False

    monkeypatch.setenv("CLOUDTAP_DB", str(tmp_path / "async-main-shared.sqlite3"))
    monkeypatch.setattr("claude_tap.cli.run_client", fake_run_client)
    monkeypatch.setattr("claude_tap.cli._open_browser", opened_urls.append)
    monkeypatch.setattr("claude_tap.cli.ensure_shared_dashboard", fake_ensure_shared_dashboard)

    args = parse_args(["--tap-output-dir", str(tmp_path), "--tap-no-update-check"])
    try:
        assert await async_main(args) == 0
        assert await async_main(args) == 0
    finally:
        for server in spawned_servers:
            await server.stop()

    assert len(opened_urls) == 1


@pytest.mark.asyncio
async def test_async_main_live_viewer_respects_tap_host(monkeypatch, tmp_path):
    """Shared dashboard startup should honor the configured tap host."""
    from claude_tap import async_main, parse_args

    dashboard_calls: list[dict[str, object]] = []

    async def fake_run_client(*args, **kwargs):
        return 0

    async def fake_ensure_shared_dashboard(*, host, port, output_dir, open_browser, open_browser_fn):
        dashboard_calls.append({"host": host, "port": port, "output_dir": output_dir, "open_browser": open_browser})
        return f"http://{host}:{port}", False

    monkeypatch.setenv("CLOUDTAP_DB", str(tmp_path / "async-main-host.sqlite3"))
    monkeypatch.setattr("claude_tap.cli.run_client", fake_run_client)
    monkeypatch.setattr("claude_tap.cli.ensure_shared_dashboard", fake_ensure_shared_dashboard)

    args = parse_args(
        [
            "--tap-output-dir",
            str(tmp_path),
            "--tap-host",
            "0.0.0.0",
            "--tap-no-open",
            "--tap-no-update-check",
        ]
    )

    assert await async_main(args) == 0
    assert dashboard_calls and dashboard_calls[0]["host"] == "0.0.0.0"


@pytest.mark.asyncio
async def test_async_main_finalizes_session_when_proxy_startup_fails(monkeypatch, tmp_path):
    """Startup bind failures should not leave active SQLite sessions behind."""
    from claude_tap import async_main, get_trace_store, parse_args

    async def fail_start(self):
        raise OSError("bind failed")

    monkeypatch.setenv("CLOUDTAP_DB", str(tmp_path / "startup-failure.sqlite3"))
    monkeypatch.setattr("claude_tap.cli.web.TCPSite.start", fail_start)

    args = parse_args(
        [
            "--tap-output-dir",
            str(tmp_path),
            "--tap-no-live",
            "--tap-no-update-check",
            "--tap-no-launch",
        ]
    )

    with pytest.raises(OSError, match="bind failed"):
        await async_main(args)

    rows = get_trace_store().list_session_rows()
    assert len(rows) == 1
    assert rows[0]["status"] == "empty"


@pytest.mark.asyncio
async def test_async_main_no_live_and_no_open_restore_non_browser_mode(monkeypatch, tmp_path):
    """--tap-no-live disables the live server and --tap-no-open prevents browser opens."""
    from claude_tap import async_main, parse_args

    opened_urls = []
    migration_calls = []

    async def fake_run_client(*args, **kwargs):
        return 0

    from unittest.mock import AsyncMock

    monkeypatch.setenv("CLOUDTAP_DB", str(tmp_path / "async-main-no-live.sqlite3"))
    monkeypatch.setattr("claude_tap.cli.run_client", fake_run_client)
    monkeypatch.setattr("claude_tap.cli._open_browser", opened_urls.append)
    monkeypatch.setattr("claude_tap.cli.migrate_legacy_traces", migration_calls.append)
    monkeypatch.setattr(
        "claude_tap.cli.ensure_shared_dashboard",
        AsyncMock(side_effect=AssertionError("dashboard should stay disabled")),
    )

    args = parse_args(["--tap-output-dir", str(tmp_path), "--tap-no-update-check", "--tap-no-live", "--tap-no-open"])
    code = await async_main(args)

    assert code == 0
    assert opened_urls == []
    assert migration_calls == [tmp_path]


def test_parse_args_allow_path_validation():
    """Test --tap-allow-path validation rejects invalid prefixes."""
    import pytest

    from claude_tap import parse_args

    # Valid prefixes
    a = parse_args(["--tap-allow-path", "/custom/api"])
    assert a.extra_allowed_paths == ["/custom/api"]

    # Empty prefix should fail
    with pytest.raises(SystemExit):
        parse_args(["--tap-allow-path", ""])

    # Prefix not starting with /
    with pytest.raises(SystemExit):
        parse_args(["--tap-allow-path", "custom/api"])

    # Root prefix /
    with pytest.raises(SystemExit):
        parse_args(["--tap-allow-path", "/"])

    # Prefix ending with /
    with pytest.raises(SystemExit):
        parse_args(["--tap-allow-path", "/custom/api/"])

    print("  test_parse_args_allow_path_validation PASSED")


FAKE_CODEX_SCRIPT = r"""#!/usr/bin/env python3
# Fake codex CLI that sends one request via OPENAI_BASE_URL
import json, os, sys, urllib.request

base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
url = f"{base}/messages"

req_body = json.dumps({
    "model": "gpt-5-codex",
    "input": "Reply with exactly: HELLO_CODEX",
}).encode()
req = urllib.request.Request(url, data=req_body, headers={
    "Content-Type": "application/json",
    "Authorization": "Bearer sk-openai-test-key-12345678",
})
try:
    with urllib.request.urlopen(req) as resp:
        body = json.loads(resp.read())
        print(f"[fake-codex] status={resp.status} id={body.get('id', '?')}")
except Exception as e:
    print(f"[fake-codex] Error: {e}", file=sys.stderr)
    sys.exit(1)

print("[fake-codex] Done.")
"""


def test_codex_client_reverse_proxy():
    """Test --tap-client codex in reverse mode using OPENAI_BASE_URL."""

    async def handler(request):
        body = await request.json()
        assert request.path == "/messages"
        from aiohttp import web

        return web.json_response(
            {
                "id": "resp_codex_1",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "HELLO_CODEX"}]}],
                "usage": {"input_tokens": 11, "output_tokens": 7},
                "model": body.get("model", "gpt-5-codex"),
            }
        )

    trace_dir = tempfile.mkdtemp(prefix="claude_tap_test_codex_")
    fake_bin_dir = tempfile.mkdtemp(prefix="fake_bin_codex_")
    fake_codex = Path(fake_bin_dir) / "codex"
    fake_codex.write_text(FAKE_CODEX_SCRIPT)
    fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IEXEC)
    stop = _start_fake_upstream(19242, handler)

    try:
        proc = _run_claude_tap(
            Path(__file__).parent,
            trace_dir,
            fake_bin_dir,
            19242,
            tap_client="codex",
        )

        assert proc.returncode == 0, f"codex mode failed: stdout={proc.stdout} stderr={proc.stderr}"
        records = read_trace_records(trace_dir)
        assert len(records) >= 1
        assert len(records) == 1
        record = records[0]
        assert record["request"]["path"] == "/v1/messages"
        assert record["upstream_base_url"] == "http://127.0.0.1:19242"
        assert record["request"]["body"]["model"] == "gpt-5-codex"
        assert "OPENAI_BASE_URL=http://127.0.0.1:" in proc.stdout
    finally:
        stop()
        _cleanup(trace_dir, fake_bin_dir, "codex")


FAKE_KIMI_SCRIPT = r"""#!/usr/bin/env python3
# Fake Kimi CLI that sends one streaming Chat Completions request via KIMI_BASE_URL
import json, os, sys, urllib.request

base = os.environ.get("KIMI_BASE_URL", "https://api.kimi.com/coding/v1")
url = f"{base}/chat/completions"

req_body = json.dumps({
    "model": "kimi-k2-turbo-preview",
    "messages": [{"role": "user", "content": "Reply with exactly: HELLO_KIMI"}],
    "stream": True,
}).encode()
req = urllib.request.Request(url, data=req_body, headers={
    "Content-Type": "application/json",
    "Authorization": "Bearer kimi-test-key-12345678",
})
try:
    with urllib.request.urlopen(req) as resp:
        chunks = resp.read().decode()
        print(f"[fake-kimi] status={resp.status} stream-bytes={len(chunks)}")
except Exception as e:
    print(f"[fake-kimi] Error: {e}", file=sys.stderr)
    sys.exit(1)

print("[fake-kimi] Done.")
"""


def test_kimi_client_reverse_proxy():
    """Test --tap-client kimi in reverse mode using KIMI_BASE_URL."""

    async def handler(request):
        body = await request.json()
        assert request.path == "/chat/completions"
        assert body["model"] == "kimi-k2-turbo-preview"
        from aiohttp import web

        resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        chunks = [
            {
                "id": "kimi_chat_1",
                "model": body["model"],
                "choices": [{"delta": {"role": "assistant", "reasoning_content": "Need exact text."}}],
            },
            {
                "id": "kimi_chat_1",
                "model": body["model"],
                "choices": [{"delta": {"content": "HELLO_KIMI"}}],
            },
            {
                "id": "kimi_chat_1",
                "model": body["model"],
                "choices": [
                    {
                        "delta": {},
                        "finish_reason": "stop",
                        "usage": {
                            "prompt_tokens": 13,
                            "completion_tokens": 2,
                            "total_tokens": 15,
                            "cached_tokens": 5,
                        },
                    }
                ],
            },
        ]
        for chunk in chunks:
            await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        await resp.write_eof()
        return resp

    trace_dir = tempfile.mkdtemp(prefix="claude_tap_test_kimi_")
    fake_bin_dir = tempfile.mkdtemp(prefix="fake_bin_kimi_")
    fake_kimi = Path(fake_bin_dir) / "kimi"
    fake_kimi.write_text(FAKE_KIMI_SCRIPT)
    fake_kimi.chmod(fake_kimi.stat().st_mode | stat.S_IEXEC)
    stop = _start_fake_upstream(19244, handler)

    try:
        proc = _run_claude_tap(
            Path(__file__).parent,
            trace_dir,
            fake_bin_dir,
            19244,
            tap_client="kimi",
        )

        assert proc.returncode == 0, f"kimi mode failed: stdout={proc.stdout} stderr={proc.stderr}"
        records = read_trace_records(trace_dir)
        assert len(records) >= 1
        assert len(records) == 1
        record = records[0]
        assert record["request"]["path"] == "/chat/completions"
        assert record["upstream_base_url"] == "http://127.0.0.1:19244"
        assert record["request"]["body"]["model"] == "kimi-k2-turbo-preview"
        assert record["response"]["body"]["content"][0]["type"] == "thinking"
        assert record["response"]["body"]["content"][1]["text"] == "HELLO_KIMI"
        assert record["response"]["body"]["usage"]["input_tokens"] == 13
        assert record["response"]["body"]["usage"]["cache_read_input_tokens"] == 5
        assert "KIMI_BASE_URL=http://127.0.0.1:" in proc.stdout
    finally:
        stop()
        _cleanup(trace_dir, fake_bin_dir, "kimi")


FAKE_KIMI_MULTITURN_SCRIPT = r"""#!/usr/bin/env python3
# Fake Kimi CLI that runs one continuous five-turn chat session.
import json, os, sys, urllib.request

base = os.environ.get("KIMI_BASE_URL", "https://api.kimi.com/coding/v1")
url = f"{base}/chat/completions"
messages = [{"role": "system", "content": "Kimi CLI regression system prompt: keep one continuous session."}]
tools = [
    {"type": "function", "function": {"name": "read_file", "description": "Read a file.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "search_code", "description": "Search source code.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "list_dir", "description": "List a directory.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "run_tests", "description": "Run tests.", "parameters": {"type": "object", "properties": {"target": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "inspect_git", "description": "Inspect git state.", "parameters": {"type": "object", "properties": {"scope": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "parse_json", "description": "Parse JSON.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}},
]

def collect_stream(stream_text):
    tool_calls = {}
    content = []
    for raw_line in stream_text.splitlines():
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        payload = line.removeprefix("data:").strip()
        if payload == "[DONE]":
            continue
        event = json.loads(payload)
        delta = event.get("choices", [{}])[0].get("delta", {})
        if delta.get("content"):
            content.append(delta["content"])
        for call in delta.get("tool_calls", []):
            idx = call.get("index", 0)
            current = tool_calls.setdefault(idx, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
            if call.get("id"):
                current["id"] = call["id"]
            if call.get("type"):
                current["type"] = call["type"]
            fn = call.get("function") or {}
            if fn.get("name"):
                current["function"]["name"] += fn["name"]
            if fn.get("arguments"):
                current["function"]["arguments"] += fn["arguments"]
    return "".join(content), [tool_calls[idx] for idx in sorted(tool_calls)]

def send_request(turn, phase):
    req_body = json.dumps({
        "model": "kimi-k2-turbo-preview",
        "messages": messages,
        "tools": tools,
        "stream": True,
    }).encode()
    req = urllib.request.Request(url, data=req_body, headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer kimi-test-key-12345678",
    })
    try:
        with urllib.request.urlopen(req) as resp:
            chunks = resp.read().decode()
            content, tool_calls = collect_stream(chunks)
            print(f"[fake-kimi] turn={turn} phase={phase} status={resp.status} tool_calls={len(tool_calls)}")
            return content, tool_calls
    except Exception as e:
        print(f"[fake-kimi] Turn {turn} {phase} error: {e}", file=sys.stderr)
        sys.exit(1)

for turn in range(1, 6):
    messages.append({"role": "user", "content": f"Kimi continuous chat turn {turn}: use at least two tools before answering."})
    _, tool_calls = send_request(turn, "tools")
    if len(tool_calls) != 2:
        print(f"[fake-kimi] expected 2 tool calls, got {len(tool_calls)}", file=sys.stderr)
        sys.exit(1)
    messages.append({"role": "assistant", "content": "", "tool_calls": tool_calls})
    for call in tool_calls:
        tool_name = call["function"]["name"]
        messages.append({
            "role": "tool",
            "tool_call_id": call["id"],
            "content": f"{tool_name} result for turn {turn}",
        })
    final_text, final_tool_calls = send_request(turn, "final")
    if final_tool_calls:
        print(f"[fake-kimi] expected final response without tool calls, got {len(final_tool_calls)}", file=sys.stderr)
        sys.exit(1)
    if f"Kimi final answer {turn}" not in final_text:
        print(f"[fake-kimi] missing final answer text for turn {turn}: {final_text}", file=sys.stderr)
        sys.exit(1)
    messages.append({"role": "assistant", "content": final_text})

print("[fake-kimi] Multi-turn Done.")
"""


def test_kimi_multiturn_tool_calls_reverse_proxy():
    """Verify Kimi reverse mode captures one continuous tool-call chat.

    The fake CLI sends five consecutive user turns in one session. Each turn
    first requests exactly two streamed tool calls, then sends the tool results
    back and receives a final assistant answer. This yields ten trace nodes and
    one accumulated Chat Completions message history.
    """

    tool_pairs = [
        ("read_file", "search_code"),
        ("list_dir", "run_tests"),
        ("inspect_git", "parse_json"),
        ("read_file", "run_tests"),
        ("search_code", "inspect_git"),
    ]

    async def handler(request):
        body = await request.json()
        assert request.path == "/chat/completions"
        assert body["model"] == "kimi-k2-turbo-preview"
        from aiohttp import web

        assert body["messages"][0] == {
            "role": "system",
            "content": "Kimi CLI regression system prompt: keep one continuous session.",
        }
        user_messages = [msg for msg in body["messages"] if msg.get("role") == "user"]
        turn = len(user_messages)
        assert 1 <= turn <= 5
        is_final_request = body["messages"][-1].get("role") == "tool"
        tool_results = [msg for msg in body["messages"] if msg.get("role") == "tool"]
        if is_final_request:
            assert len(tool_results) == turn * 2
        else:
            assert body["messages"][-1]["content"].startswith(f"Kimi continuous chat turn {turn}:")
            assert len(tool_results) == (turn - 1) * 2

        names = tool_pairs[turn - 1]

        resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        if is_final_request:
            chunk = {
                "id": f"kimi_multi_{turn}_final",
                "model": body["model"],
                "choices": [
                    {
                        "delta": {
                            "role": "assistant",
                            "content": f"Kimi final answer {turn}: used {names[0]} and {names[1]}.",
                        },
                        "finish_reason": "stop",
                        "usage": {
                            "prompt_tokens": 150 + turn,
                            "completion_tokens": 30 + turn,
                            "total_tokens": 180 + (turn * 2),
                            "cached_tokens": 20 + turn,
                        },
                    }
                ],
            }
            await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
        else:
            for idx, name in enumerate(names):
                arguments = json.dumps({"turn": turn, "tool": name})
                chunk = {
                    "id": f"kimi_multi_{turn}_tools",
                    "model": body["model"],
                    "choices": [
                        {
                            "delta": {
                                "role": "assistant" if idx == 0 else None,
                                "tool_calls": [
                                    {
                                        "index": idx,
                                        "id": f"call_{turn}_{idx + 1}",
                                        "type": "function",
                                        "function": {"name": name, "arguments": arguments},
                                    }
                                ],
                            }
                        }
                    ],
                }
                if idx == 1:
                    chunk["choices"][0]["finish_reason"] = "tool_calls"
                    chunk["choices"][0]["usage"] = {
                        "prompt_tokens": 100 + turn,
                        "completion_tokens": 20 + turn,
                        "total_tokens": 120 + (turn * 2),
                        "cached_tokens": 10 + turn,
                    }
                await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        await resp.write_eof()
        return resp

    trace_dir = tempfile.mkdtemp(prefix="claude_tap_test_kimi_multiturn_")
    fake_bin_dir = tempfile.mkdtemp(prefix="fake_bin_kimi_multiturn_")
    fake_kimi = Path(fake_bin_dir) / "kimi"
    fake_kimi.write_text(FAKE_KIMI_MULTITURN_SCRIPT)
    fake_kimi.chmod(fake_kimi.stat().st_mode | stat.S_IEXEC)
    stop = _start_fake_upstream(19245, handler)

    try:
        proc = _run_claude_tap(
            Path(__file__).parent,
            trace_dir,
            fake_bin_dir,
            19245,
            tap_client="kimi",
        )

        assert proc.returncode == 0, f"kimi multi-turn mode failed: stdout={proc.stdout} stderr={proc.stderr}"
        records = read_trace_records(trace_dir)
        assert len(records) >= 1
        assert len(records) == 10

        unique_tool_names = set()
        total_tool_calls = 0
        for turn in range(1, 6):
            tool_record = records[(turn - 1) * 2]
            final_record = records[(turn - 1) * 2 + 1]
            for record in (tool_record, final_record):
                assert record["request"]["path"] == "/chat/completions"
                assert record["upstream_base_url"] == "http://127.0.0.1:19245"
                assert record["request"]["body"]["messages"][0]["role"] == "system"

            assert tool_record["request"]["body"]["messages"][-1]["content"].startswith(
                f"Kimi continuous chat turn {turn}:"
            )
            tool_blocks = [
                block for block in tool_record["response"]["body"]["content"] if block.get("type") == "tool_use"
            ]
            assert len(tool_blocks) == 2
            total_tool_calls += len(tool_blocks)
            unique_tool_names.update(block["name"] for block in tool_blocks)
            assert tool_record["response"]["body"]["usage"]["cache_read_input_tokens"] == 10 + turn

            assert final_record["request"]["body"]["messages"][-1]["role"] == "tool"
            final_tool_blocks = [
                block for block in final_record["response"]["body"]["content"] if block.get("type") == "tool_use"
            ]
            assert final_tool_blocks == []
            final_text = " ".join(
                block.get("text", "")
                for block in final_record["response"]["body"]["content"]
                if block.get("type") == "text"
            )
            assert f"Kimi final answer {turn}" in final_text
            assert final_record["response"]["body"]["usage"]["cache_read_input_tokens"] == 20 + turn

        expected_tool_names = {"read_file", "search_code", "list_dir", "run_tests", "inspect_git", "parse_json"}
        assert total_tool_calls == 10
        assert unique_tool_names == expected_tool_names
        assert "KIMI_BASE_URL=http://127.0.0.1:" in proc.stdout
    finally:
        stop()
        _cleanup(trace_dir, fake_bin_dir, "kimi_multiturn")


## ---------------------------------------------------------------------------
## Test 6b: test_codex_zstd_request_body — proxy decompresses zstd request bodies
## ---------------------------------------------------------------------------


def test_codex_zstd_request_body():
    """Verify the proxy decompresses zstd-encoded request bodies from Codex CLI."""
    received_bodies: list[dict] = []

    async def handler(request):
        body = await request.json()
        received_bodies.append(body)
        # Content-Encoding: zstd should have been stripped by the proxy
        assert "zstd" not in request.headers.get("Content-Encoding", "").lower()
        from aiohttp import web

        return web.json_response(
            {
                "id": "resp_zstd_1",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "OK"}]}],
                "usage": {"input_tokens": 5, "output_tokens": 2},
                "model": "gpt-5-codex",
            }
        )

    # Build a fake codex script that sends a zstd-compressed body
    zstd_codex_script = r"""#!/usr/bin/env python3
import json, os, sys, urllib.request
import backports.zstd

base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
url = f"{base}/responses"
payload = json.dumps({"model": "gpt-5-codex", "input": "zstd test"}).encode()
compressed = backports.zstd.compress(payload)

req = urllib.request.Request(url, data=compressed, headers={
    "Content-Type": "application/json",
    "Content-Encoding": "zstd",
    "Authorization": "Bearer sk-test",
})
try:
    with urllib.request.urlopen(req) as resp:
        print(f"[fake-codex] status={resp.status}")
except Exception as e:
    print(f"[fake-codex] Error: {e}", file=sys.stderr)
    sys.exit(1)
"""

    trace_dir = tempfile.mkdtemp(prefix="claude_tap_test_zstd_")
    fake_bin_dir = tempfile.mkdtemp(prefix="fake_bin_zstd_")
    fake_codex = Path(fake_bin_dir) / "codex"
    fake_codex.write_text(zstd_codex_script)
    fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IEXEC)
    stop = _start_fake_upstream(19243, handler)

    try:
        proc = _run_claude_tap(
            Path(__file__).parent,
            trace_dir,
            fake_bin_dir,
            19243,
            tap_client="codex",
        )

        assert proc.returncode == 0, f"zstd test failed: stdout={proc.stdout} stderr={proc.stderr}"
        assert len(received_bodies) == 1
        assert received_bodies[0]["input"] == "zstd test"
    finally:
        stop()
        _cleanup(trace_dir, fake_bin_dir, "codex")


## ---------------------------------------------------------------------------
## Test 6b: test_codex_zstd_request_body — proxy decompresses zstd request bodies
## ---------------------------------------------------------------------------
def test_filter_headers():
    """Test filter_headers strips hop-by-hop headers and optionally redacts secrets."""
    from claude_tap import filter_headers

    headers = {
        "Content-Type": "application/json",
        "x-api-key": "sk-ant-api03-very-long-secret-key-12345678",
        "Authorization": "Bearer sk-ant-secret-token-abcdef",
        "Cookie": "session=qoder-cookie-secret",
        "Set-Cookie": "acw_tc=qoder-response-cookie-secret; Path=/",
        "Cosy-Key": "cosy-signature-secret-value",
        "Cosy-MachineToken": "qoder-machine-token-secret-value",
        "Cosy-MachineId": "qoder-machine-id-secret-value",
        "Cosy-MachineType": "qoder-machine-type-secret-value",
        "Cosy-User": "qoder-user-id-secret-value",
        "Transfer-Encoding": "chunked",
        "Connection": "keep-alive",
        "X-Custom": "custom-value",
    }

    # Without redaction
    out = filter_headers(headers, redact_keys=False)
    assert "Transfer-Encoding" not in out, "hop-by-hop not filtered"
    assert "Connection" not in out, "hop-by-hop not filtered"
    assert out["x-api-key"] == headers["x-api-key"], "should not redact without flag"
    assert out["Cosy-Key"] == headers["Cosy-Key"], "should not redact without flag"
    assert out["X-Custom"] == "custom-value"
    print("  OK: hop-by-hop filtered, no redaction")

    # With redaction
    out = filter_headers(headers, redact_keys=True)
    assert out["x-api-key"].endswith("...")
    assert "very-long-secret" not in out["x-api-key"]
    assert out["Authorization"].endswith("...")
    assert "secret-token" not in out["Authorization"]
    assert out["Cookie"] == "***"
    assert "cookie-secret" not in out["Cookie"]
    assert out["Set-Cookie"] == "***"
    assert "response-cookie-secret" not in out["Set-Cookie"]
    assert out["Cosy-Key"] == "***"
    assert "signature-secret" not in out["Cosy-Key"]
    assert out["Cosy-MachineToken"] == "***"
    assert "machine-token-secret" not in out["Cosy-MachineToken"]
    assert out["Cosy-MachineId"] == "***"
    assert "machine-id-secret" not in out["Cosy-MachineId"]
    assert out["Cosy-MachineType"] == "***"
    assert "machine-type-secret" not in out["Cosy-MachineType"]
    assert out["Cosy-User"] == "***"
    assert "user-id-secret" not in out["Cosy-User"]
    assert out["Content-Type"] == "application/json"
    assert out["X-Custom"] == "custom-value"
    print("  OK: secrets redacted")

    # Short key gets fully masked
    short_headers = {"x-api-key": "short"}
    out = filter_headers(short_headers, redact_keys=True)
    assert out["x-api-key"] == "***"
    print("  OK: short key masked")

    print("\n  test_filter_headers PASSED")


## ---------------------------------------------------------------------------
## Test 8: test_sse_reassembler — unit test SSE parsing edge cases
## ---------------------------------------------------------------------------


def test_sse_reassembler():
    """Test SSEReassembler handles various edge cases correctly."""
    from claude_tap import SSEReassembler

    # Basic: valid events
    r = SSEReassembler()
    r.feed_bytes(
        b'event: message_start\ndata: {"type":"message_start","message":{"id":"m1","type":"message","role":"assistant","content":[],"model":"test","usage":{"input_tokens":10,"output_tokens":0}}}\n\n'
    )
    r.feed_bytes(
        b'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n'
    )
    r.feed_bytes(
        b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"hello"}}\n\n'
    )
    r.feed_bytes(b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n')
    r.feed_bytes(
        b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":1}}\n\n'
    )
    r.feed_bytes(b'event: message_stop\ndata: {"type":"message_stop"}\n\n')
    body = r.reconstruct()
    assert body is not None
    assert body["content"][0]["text"] == "hello"
    assert len(r.events) == 6
    print("  OK: basic SSE parsing")

    # Bare data line (no event: prefix) — must be emitted as a default-type
    # event so OpenAI Chat Completions streams (which never send event: headers)
    # don't get silently dropped.
    r2 = SSEReassembler()
    r2.feed_bytes(b'data: {"orphan": true}\n\n')
    # Bare data lines (no event: prefix) are emitted as default-type events.
    # OpenAI Chat Completions streams use exactly this shape — the previous
    # "ignored" behavior silently dropped every such response body.
    assert len(r2.events) == 1
    assert r2.events[0]["event"] == "message"
    assert r2.events[0]["data"] == {"orphan": True}
    # Snapshot reconstruction stays a no-op for non-Anthropic/Responses schemas.
    assert r2.reconstruct() is None
    print("  OK: bare data emitted as default-type event")

    # Partial chunks (data split across feed_bytes calls)
    r3 = SSEReassembler()
    r3.feed_bytes(b"event: message_st")
    r3.feed_bytes(b'art\ndata: {"type":"mess')
    r3.feed_bytes(
        b'age_start","message":{"id":"m2","type":"message","role":"assistant","content":[],"model":"t","usage":{"input_tokens":1,"output_tokens":0}}}\n\n'
    )
    assert len(r3.events) == 1
    assert r3.events[0]["event"] == "message_start"
    print("  OK: chunked data reassembly")

    # Invalid JSON in data — stored as string
    r4 = SSEReassembler()
    r4.feed_bytes(b"event: bad_event\ndata: {broken json\n\n")
    assert len(r4.events) == 1
    assert r4.events[0]["data"] == "{broken json"
    print("  OK: invalid JSON stored as string")

    # Empty stream
    r5 = SSEReassembler()
    r5.feed_bytes(b"")
    assert len(r5.events) == 0
    assert r5.reconstruct() is None
    print("  OK: empty stream")

    print("\n  test_sse_reassembler PASSED")


## ---------------------------------------------------------------------------
## Test 9: test_upstream_unreachable — proxy returns 502
## ---------------------------------------------------------------------------

FAKE_UPSTREAM_UNREACHABLE_PORT = 19204

FAKE_CLAUDE_UNREACHABLE_SCRIPT = r'''#!/usr/bin/env python3
"""Fake claude CLI — sends a request to a dead upstream."""
import json, os, sys, urllib.request, urllib.error

base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
url = f"{base}/v1/messages"

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
        print(f"[fake-claude] Got response: {resp.status}")
except urllib.error.HTTPError as e:
    print(f"[fake-claude] HTTP {e.code}: {e.read().decode()}")
except Exception as e:
    print(f"[fake-claude] Error: {e}", file=sys.stderr)
    sys.exit(1)

print("[fake-claude] Done.")
'''


def test_upstream_unreachable():
    """Test that when upstream is unreachable (connection refused), the proxy
    returns 502 and the trace contains no records (since we can't reach upstream)."""

    project_dir = PROJECT_ROOT
    trace_dir = tempfile.mkdtemp(prefix="claude_tap_test_unreachable_")
    fake_bin_dir = _create_fake_claude(FAKE_CLAUDE_UNREACHABLE_SCRIPT)

    # Point --tap-target at a port that nothing is listening on
    env = os.environ.copy()
    env["PATH"] = fake_bin_dir + ":" + env.get("PATH", "")

    env = e2e_env(env, trace_dir)
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "claude_tap",
                "--tap-output-dir",
                trace_dir,
                "--tap-no-open",
                "--tap-target",
                f"http://127.0.0.1:{FAKE_UPSTREAM_UNREACHABLE_PORT}",
            ],
            cwd=str(project_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

        print(f"[test_upstream_unreachable] Exit code: {proc.returncode}")
        if proc.stdout.strip():
            print(f"[test_upstream_unreachable] stdout:\n{proc.stdout.rstrip()}")
        if proc.stderr.strip():
            print(f"[test_upstream_unreachable] stderr:\n{proc.stderr.rstrip()}")

        # The proxy should still produce summary output
        assert "Trace summary" in proc.stdout
        print("  OK: proxy did not crash")

        # No trace records (502 returned in-process, not from upstream)
        records = read_trace_records(trace_dir)
        assert len(records) == 0, f"Expected 0 records, got {len(records)}"
        print("  OK: no trace records (upstream unreachable, 502 returned)")

        log_content = read_proxy_log(trace_dir)
        assert log_content.strip()
        assert "upstream error" in log_content.lower() or "connect" in log_content.lower(), (
            f"Expected upstream error in log, got: {log_content[:200]}"
        )
        print("  OK: upstream error logged")

        print("\n  test_upstream_unreachable PASSED")

    except subprocess.TimeoutExpired:
        print("[test_upstream_unreachable] TIMEOUT")
        sys.exit(1)
    finally:
        _cleanup(trace_dir, fake_bin_dir, "unreachable")


## ---------------------------------------------------------------------------
## Test: version check helpers
## ---------------------------------------------------------------------------


def test_version_tuple():
    """Test _version_tuple parsing."""
    from claude_tap import _version_tuple

    assert _version_tuple("0.1.4") == (0, 1, 4)
    assert _version_tuple("1.0.0") == (1, 0, 0)
    assert _version_tuple("10.20.30") == (10, 20, 30)
    assert _version_tuple("0.1.4") < _version_tuple("0.2.0")
    assert _version_tuple("1.0.0") > _version_tuple("0.99.99")
    print("  test_version_tuple PASSED")


def test_detect_installer():
    """Test _detect_installer returns 'uv' or 'pip'."""
    from claude_tap import _detect_installer

    result = _detect_installer()
    assert result in ("uv", "pip"), f"Unexpected installer: {result}"
    print(f"  test_detect_installer: detected '{result}' — PASSED")


## ---------------------------------------------------------------------------
## Test: version check with fake PyPI
## ---------------------------------------------------------------------------

FAKE_PYPI_PORT = 19210

# Minimal fake claude that exits immediately without making any upstream
# requests.  Used by version-check tests which only care about the update
# banner printed by claude-tap itself, not about proxied API traffic.
FAKE_CLAUDE_NOOP_SCRIPT = r'''#!/usr/bin/env python3
"""Fake claude CLI -- exits immediately (no network calls)."""
print("[fake-claude] noop exit")
'''


@pytest.mark.slow
def test_version_check_with_fake_pypi():
    """Test that update check detects a newer version from a fake PyPI server."""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class FakePyPI(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"info": {"version": "99.0.0"}}).encode())

        def log_message(self, format, *args):
            pass  # silence logs

    server = HTTPServer(("127.0.0.1", FAKE_PYPI_PORT), FakePyPI)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    project_dir = PROJECT_ROOT
    trace_dir = tempfile.mkdtemp(prefix="claude_tap_test_update_")
    fake_bin_dir = _create_fake_claude(FAKE_CLAUDE_NOOP_SCRIPT)

    try:
        env = os.environ.copy()
        env["PATH"] = fake_bin_dir + ":" + env.get("PATH", "")
        env = e2e_env(env, trace_dir)
        env["CLAUDE_TAP_PYPI_URL"] = f"http://127.0.0.1:{FAKE_PYPI_PORT}/pypi/claude-tap/json"

        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "claude_tap",
                "--tap-output-dir",
                trace_dir,
                "--tap-no-open",
                "--tap-target",
                "http://127.0.0.1:1",  # dummy target; noop client never connects
                "--tap-no-auto-update",
            ],
            cwd=str(project_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert "Update available" in proc.stdout, f"Expected 'Update available' in stdout:\n{proc.stdout}"
        assert "99.0.0" in proc.stdout
        print("  OK: update available detected")
        print("  test_version_check_with_fake_pypi PASSED")
    except subprocess.TimeoutExpired as exc:
        raise AssertionError("claude_tap subprocess timed out (30s) — possible port conflict or hang") from exc
    finally:
        server.shutdown()
        _cleanup(trace_dir, fake_bin_dir, "update_check")


FAKE_PYPI_NOCHECK_PORT = 19211


def test_version_check_no_update():
    """Test that no update message when current version matches PyPI."""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from claude_tap import __version__

    class FakePyPICurrent(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"info": {"version": __version__}}).encode())

        def log_message(self, format, *args):
            pass

    server = HTTPServer(("127.0.0.1", FAKE_PYPI_NOCHECK_PORT), FakePyPICurrent)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    project_dir = PROJECT_ROOT
    trace_dir = tempfile.mkdtemp(prefix="claude_tap_test_noupdate_")
    fake_bin_dir = _create_fake_claude(FAKE_CLAUDE_NOOP_SCRIPT)

    try:
        env = os.environ.copy()
        env["PATH"] = fake_bin_dir + ":" + env.get("PATH", "")
        env = e2e_env(env, trace_dir)
        env["CLAUDE_TAP_PYPI_URL"] = f"http://127.0.0.1:{FAKE_PYPI_NOCHECK_PORT}/pypi/claude-tap/json"

        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "claude_tap",
                "--tap-output-dir",
                trace_dir,
                "--tap-no-open",
                "--tap-target",
                "http://127.0.0.1:1",  # dummy target; noop client never connects
            ],
            cwd=str(project_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert "Update available" not in proc.stdout, f"Unexpected 'Update available' in stdout:\n{proc.stdout}"
        print("  OK: no update message when version matches")
        print("  test_version_check_no_update PASSED")
    except subprocess.TimeoutExpired as exc:
        raise AssertionError("claude_tap subprocess timed out (30s) — possible port conflict or hang") from exc
    finally:
        server.shutdown()
        _cleanup(trace_dir, fake_bin_dir, "no_update")


## ---------------------------------------------------------------------------
## Test: trace cleanup
## ---------------------------------------------------------------------------


def test_trace_cleanup():
    """Test cleanup_trace_sessions removes oldest sessions while keeping newest."""
    from claude_tap import cleanup_trace_sessions, get_trace_store, reset_trace_store

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "cleanup.sqlite3"
        os.environ["CLOUDTAP_DB"] = str(db_path)
        reset_trace_store()
        store = get_trace_store()
        session_ids = [store.create_session(client="claude", proxy_mode="reverse") for _ in range(5)]
        for session_id in session_ids:
            store.finalize_session(session_id, {"api_calls": 1})

        removed = cleanup_trace_sessions(3)
        assert removed == 2, f"Expected 2 removed, got {removed}"
        assert len(store.list_session_rows()) == 3
        remaining = {row["id"] for row in store.list_session_rows()}
        assert session_ids[0] not in remaining
        assert session_ids[1] not in remaining
        assert session_ids[-1] in remaining

        print("  test_trace_cleanup PASSED")


def test_trace_tagging_safety():
    """Test that cleanup only removes stored sessions."""
    from claude_tap import cleanup_trace_sessions, get_trace_store, reset_trace_store

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "cleanup.sqlite3"
        os.environ["CLOUDTAP_DB"] = str(db_path)
        reset_trace_store()
        store = get_trace_store()
        for _ in range(5):
            session_id = store.create_session(client="claude", proxy_mode="reverse")
            store.finalize_session(session_id, {"api_calls": 1})

        removed = cleanup_trace_sessions(2)
        assert removed == 3
        assert len(store.list_session_rows()) == 2

        print("  test_trace_tagging_safety PASSED")


def test_manifest_migration():
    """Test that existing trace files without SQLite rows are migrated."""
    from claude_tap import get_trace_store, migrate_legacy_traces, reset_trace_store

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir)
        db_path = output_dir / "migrate.sqlite3"
        os.environ["CLOUDTAP_DB"] = str(db_path)
        reset_trace_store()

        for i in range(4):
            ts = f"20260218_02000{i}"
            date_dir = output_dir / "2026-02-18"
            date_dir.mkdir(parents=True, exist_ok=True)
            (date_dir / f"trace_{ts.split('_')[1]}.jsonl").write_text(
                json.dumps({"request_id": ts, "request": {}, "response": {}}) + "\n"
            )
            (date_dir / f"trace_{ts.split('_')[1]}.log").write_text("log")

        imported = migrate_legacy_traces(output_dir)
        assert imported == 4
        assert len(get_trace_store().list_session_rows()) == 4

        print("  test_manifest_migration PASSED")


def test_e2e_with_cleanup():
    """E2E test: pre-fill sessions, run claude-tap with --tap-max-traces, verify cleanup."""
    from claude_tap import get_trace_store, reset_trace_store

    stop_upstream, upstream_port = run_fake_upstream_in_thread()

    project_dir = PROJECT_ROOT
    trace_dir = tempfile.mkdtemp(prefix="claude_tap_test_cleanup_")
    output_dir = Path(trace_dir)
    fake_bin_dir = _create_fake_claude(FAKE_CLAUDE_SCRIPT)

    try:
        db_path = output_dir / "claude-tap-test.sqlite3"
        os.environ["CLOUDTAP_DB"] = str(db_path)
        reset_trace_store()
        store = get_trace_store()
        for _ in range(4):
            session_id = store.create_session(client="claude", proxy_mode="reverse")
            store.finalize_session(session_id, {"api_calls": 1})

        env = os.environ.copy()
        env["PATH"] = fake_bin_dir + ":" + env.get("PATH", "")
        env["CLAUDE_TAP_PYPI_URL"] = "http://127.0.0.1:1/invalid"
        env = e2e_env(env, trace_dir)

        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "claude_tap",
                "--tap-output-dir",
                trace_dir,
                "--tap-no-open",
                "--tap-target",
                f"http://127.0.0.1:{upstream_port}",
                "--tap-max-traces",
                "3",
                "--tap-no-update-check",
            ],
            cwd=str(project_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

        print(f"[test_e2e_with_cleanup] Exit code: {proc.returncode}")
        if proc.stdout.strip():
            print(f"[test_e2e_with_cleanup] stdout:\n{proc.stdout.rstrip()}")

        assert proc.returncode == 0
        assert "Cleaned up" in proc.stdout, f"Expected cleanup message in stdout:\n{proc.stdout}"
        reset_trace_store()
        os.environ["CLOUDTAP_DB"] = str(db_path)
        assert len(get_trace_store().list_session_rows()) == 3

        print("  test_e2e_with_cleanup PASSED")

    except subprocess.TimeoutExpired:
        print("  TIMEOUT")
    finally:
        stop_upstream()
        _cleanup(trace_dir, fake_bin_dir, "e2e_cleanup")


## ---------------------------------------------------------------------------
## Test: viewer bug fixes (HTML content verification)
## ---------------------------------------------------------------------------


def test_live_viewer_scroll_preservation():
    """Verify viewer.html contains preserveDetail parameter chain for scroll fix."""
    viewer_path = Path(__file__).parent.parent / "claude_tap" / "viewer.html"
    html = viewer_path.read_text(encoding="utf-8")

    # selectEntry should accept opts parameter
    assert "function selectEntry(idx, opts)" in html, "selectEntry should accept opts parameter"
    # renderApp should accept preserveDetail
    assert "function renderApp(preserveDetail)" in html, "renderApp should accept preserveDetail"
    # applyFilter should accept preserveDetail
    assert "function applyFilter(preserveDetail)" in html, "applyFilter should accept preserveDetail"
    # renderSidebar should accept preserveDetail
    assert "function renderSidebar(preserveDetail)" in html, "renderSidebar should accept preserveDetail"
    # currentDetailRequestId tracking
    assert "currentDetailRequestId" in html, "Should track currentDetailRequestId"
    # SSE handler should pass true to renderApp
    assert "renderApp(true)" in html, "SSE handler should call renderApp(true)"

    print("  test_live_viewer_scroll_preservation PASSED")


def test_live_viewer_diff_nav_update():
    """Verify viewer.html contains dynamic diff nav button update logic."""
    viewer_path = Path(__file__).parent.parent / "claude_tap" / "viewer.html"
    html = viewer_path.read_text(encoding="utf-8")

    # updateNavButtons function should exist
    assert "function updateNavButtons()" in html, "Should have updateNavButtons function"
    # setInterval for live mode
    assert "setInterval(updateNavButtons" in html, "Should have setInterval for updateNavButtons in live mode"
    # clearInterval on close
    assert "clearInterval(navInterval)" in html, "Should clear interval on close"

    print("  test_live_viewer_diff_nav_update PASSED")


@pytest.mark.asyncio
async def test_live_viewer_sse_incremental():
    """Test that LiveViewerServer correctly handles incremental SSE broadcasts."""
    import aiohttp

    from claude_tap import LiveViewerServer

    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["CLOUDTAP_DB"] = str(Path(tmpdir) / "live.sqlite3")
        from claude_tap.trace_store import get_trace_store, reset_trace_store

        reset_trace_store()
        session_id = get_trace_store().create_session()
        server = LiveViewerServer(session_id=session_id, port=0)
        port = await server.start()

        try:
            # Broadcast multiple records
            for i in range(5):
                await server.broadcast({"turn": i + 1, "request_id": f"req_{i}", "request": {"method": "POST"}})

            async with aiohttp.ClientSession() as session:
                async with session.get(f"http://127.0.0.1:{port}/records") as resp:
                    records = await resp.json()
                    assert len(records) == 5, f"Expected 5 records, got {len(records)}"
                    assert records[0]["turn"] == 1
                    assert records[4]["turn"] == 5
                    print("  OK: 5 incremental records via /records")

        finally:
            await server.stop()

        print("  test_live_viewer_sse_incremental PASSED")


## ---------------------------------------------------------------------------
## Test: parse_args with new flags
## ---------------------------------------------------------------------------


def test_parse_args_new_flags():
    """Test --tap-max-traces, --tap-no-update-check, --tap-no-auto-update flags."""
    from claude_tap import parse_args

    # Defaults
    a = parse_args([])
    assert a.max_traces == 50
    assert a.no_update_check is False
    assert a.no_auto_update is False
    print("  OK: new flag defaults")

    # Set max traces
    a = parse_args(["--tap-max-traces", "100"])
    assert a.max_traces == 100
    print("  OK: --tap-max-traces 100")

    # Unlimited traces
    a = parse_args(["--tap-max-traces", "0"])
    assert a.max_traces == 0
    print("  OK: --tap-max-traces 0")

    # Disable update check
    a = parse_args(["--tap-no-update-check"])
    assert a.no_update_check is True
    print("  OK: --tap-no-update-check")

    # Only check, no auto-update
    a = parse_args(["--tap-no-auto-update"])
    assert a.no_auto_update is True
    print("  OK: --tap-no-auto-update")

    # Mix with claude args
    a = parse_args(["--tap-max-traces", "20", "--tap-no-update-check", "-c", "--model", "opus"])
    assert a.max_traces == 20
    assert a.no_update_check is True
    assert a.claude_args == ["-c", "--model", "opus"]
    print("  OK: mixed new + claude flags")

    print("  test_parse_args_new_flags PASSED")


def test_parse_dashboard_args():
    """Test standalone dashboard argument parsing."""
    from claude_tap import parse_dashboard_args

    a = parse_dashboard_args([])
    assert a.output_dir == "./.traces"
    assert a.live_port == 0
    assert a.host == "127.0.0.1"
    assert a.open_viewer is True

    a = parse_dashboard_args(
        ["--tap-output-dir", "/tmp/t", "--tap-live-port", "3000", "--tap-host", "0.0.0.0", "--tap-no-open"]
    )
    assert a.output_dir == "/tmp/t"
    assert a.live_port == 3000
    assert a.host == "0.0.0.0"
    assert a.open_viewer is False

    print("  test_parse_dashboard_args PASSED")


## ---------------------------------------------------------------------------
## Test: CA certificate generation
## ---------------------------------------------------------------------------


def test_cert_generation():
    """Test CA and per-host certificate generation."""
    from claude_tap.certs import CertificateAuthority, ensure_ca

    with tempfile.TemporaryDirectory() as tmpdir:
        ca_dir = Path(tmpdir)
        ca_cert_path, ca_key_path = ensure_ca(ca_dir)

        # CA files exist
        assert ca_cert_path.exists(), "CA cert not created"
        assert ca_key_path.exists(), "CA key not created"
        assert ca_cert_path.name == "ca.pem"
        assert ca_key_path.name == "ca-key.pem"
        print("  OK: CA files created")

        # Key has restricted permissions (owner-only)
        key_mode = ca_key_path.stat().st_mode & 0o777
        assert key_mode == 0o600, f"CA key permissions too open: {oct(key_mode)}"
        print("  OK: CA key permissions restricted")

        # Calling ensure_ca again reuses existing files
        ca_cert_path2, ca_key_path2 = ensure_ca(ca_dir)
        assert ca_cert_path2 == ca_cert_path
        assert ca_cert_path2.read_bytes() == ca_cert_path.read_bytes()
        print("  OK: ensure_ca reuses existing CA")

        # Generate host cert
        ca = CertificateAuthority(ca_cert_path, ca_key_path)
        cert_pem, key_pem = ca.get_host_cert_pem("api.anthropic.com")
        assert b"BEGIN CERTIFICATE" in cert_pem
        assert b"BEGIN RSA PRIVATE KEY" in key_pem
        print("  OK: host cert generated for api.anthropic.com")

        # Cache hit
        cert_pem2, key_pem2 = ca.get_host_cert_pem("api.anthropic.com")
        assert cert_pem2 is cert_pem  # Same object (cached)
        print("  OK: host cert cached")

        # Different host gets different cert
        cert_pem3, _ = ca.get_host_cert_pem("example.com")
        assert cert_pem3 != cert_pem
        print("  OK: different host gets different cert")

        # SSL context creation
        ssl_ctx = ca.make_ssl_context("api.anthropic.com")
        import ssl

        assert isinstance(ssl_ctx, ssl.SSLContext)
        print("  OK: SSL context created")

    print("  test_cert_generation PASSED")


def test_parse_args_proxy_mode():
    """Test --tap-proxy-mode flag parsing."""
    from claude_tap import parse_args

    # Default is reverse
    a = parse_args([])
    assert a.proxy_mode == "reverse"
    print("  OK: default proxy_mode is 'reverse'")

    # Explicit reverse
    a = parse_args(["--tap-proxy-mode", "reverse"])
    assert a.proxy_mode == "reverse"
    print("  OK: --tap-proxy-mode reverse")

    # Forward mode
    a = parse_args(["--tap-proxy-mode", "forward"])
    assert a.proxy_mode == "forward"
    print("  OK: --tap-proxy-mode forward")

    # Mix with other flags
    a = parse_args(["--tap-proxy-mode", "forward", "--tap-port", "8080", "-p", "hello"])
    assert a.proxy_mode == "forward"
    assert a.port == 8080
    assert a.claude_args == ["-p", "hello"]
    print("  OK: forward mode with other flags")

    print("  test_parse_args_proxy_mode PASSED")


## ---------------------------------------------------------------------------
## Test: Codex upstream URL construction for all (target × path) combos
## ---------------------------------------------------------------------------


def test_codex_upstream_url_construction(monkeypatch, tmp_path):
    """Verify that strip_path_prefix produces correct upstream URLs for all Codex backends.

    This is a regression guard for the bug where strip_path_prefix="/v1" combined
    with target="https://api.openai.com" produced wrong URLs like
    https://api.openai.com/responses instead of https://api.openai.com/v1/responses.

    See: .agents/docs/error-experience/entries/2026-03-10-codex-strip-prefix-url-mismatch.md
    """
    from claude_tap import parse_args

    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    def _build_upstream(target: str, strip_prefix: str, request_path: str) -> str:
        fwd_path = request_path
        if strip_prefix and fwd_path.startswith(strip_prefix):
            fwd_path = fwd_path[len(strip_prefix) :] or "/"
        return target.rstrip("/") + "/" + fwd_path.lstrip("/")

    # Case 1: API Key mode — target=api.openai.com, should NOT strip /v1
    args = parse_args(["--tap-client", "codex"])
    strip = "/v1" if args.client == "codex" and "api.openai.com" not in args.target else ""
    url = _build_upstream(args.target, strip, "/v1/responses")
    assert url == "https://api.openai.com/v1/responses", f"API Key mode URL wrong: {url}"
    print("  OK: api.openai.com + /v1/responses → correct")

    # Case 2: OAuth mode — target=chatgpt.com, should strip /v1
    args = parse_args(["--tap-client", "codex", "--tap-target", "https://chatgpt.com/backend-api/codex"])
    strip = "/v1" if args.client == "codex" and "api.openai.com" not in args.target else ""
    url = _build_upstream(args.target, strip, "/v1/responses")
    assert url == "https://chatgpt.com/backend-api/codex/responses", f"OAuth mode URL wrong: {url}"
    print("  OK: chatgpt.com + /v1/responses → correct")

    # Case 3: API Key mode with /v1/models path
    args = parse_args(["--tap-client", "codex"])
    strip = "/v1" if args.client == "codex" and "api.openai.com" not in args.target else ""
    url = _build_upstream(args.target, strip, "/v1/models")
    assert url == "https://api.openai.com/v1/models", f"API Key models URL wrong: {url}"
    print("  OK: api.openai.com + /v1/models → correct")

    # Case 4: OAuth mode with /v1/models path
    args = parse_args(["--tap-client", "codex", "--tap-target", "https://chatgpt.com/backend-api/codex"])
    strip = "/v1" if args.client == "codex" and "api.openai.com" not in args.target else ""
    url = _build_upstream(args.target, strip, "/v1/models")
    assert url == "https://chatgpt.com/backend-api/codex/models", f"OAuth models URL wrong: {url}"
    print("  OK: chatgpt.com + /v1/models → correct")

    # Case 5: Claude client should never strip
    args = parse_args(["--tap-client", "claude"])
    strip = "/v1" if args.client == "codex" and "api.openai.com" not in args.target else ""
    assert strip == "", "Claude client should never strip path prefix"
    print("  OK: claude client has no strip prefix")

    print("  test_codex_upstream_url_construction PASSED")


## ---------------------------------------------------------------------------
## Test: Forward proxy CONNECT handler
## ---------------------------------------------------------------------------


def test_forward_proxy_trace_skip_rules_are_narrow():
    from claude_tap.forward_proxy import _should_skip_trace_record

    json_headers = {"Content-Type": "application/json"}
    binary_headers = {"Content-Type": "application/octet-stream"}
    npm_headers = {"User-Agent": "npm/10.8.2 node/v22.0.0 linux x64 workspaces/false"}

    assert _should_skip_trace_record("https://registry.npmjs.org:443/effect", "/effect", json_headers)
    assert _should_skip_trace_record(
        "https://registry.npmjs.org:443/@opencode-ai%2fsdk",
        "/@opencode-ai%2fsdk",
        json_headers,
    )
    assert _should_skip_trace_record(
        "https://cdn.example.test:443/effect/-/effect-4.0.0-beta.59.tgz",
        "/effect/-/effect-4.0.0-beta.59.tgz",
        binary_headers,
    )
    assert _should_skip_trace_record(
        "https://npm.mycorp.internal:443/private-pkg",
        "/private-pkg",
        json_headers,
        npm_headers,
        "GET",
    )

    assert not _should_skip_trace_record("https://api.anthropic.com:443/v1/messages", "/v1/messages", json_headers)
    assert not _should_skip_trace_record(
        "https://chatgpt.com:443/backend-api/codex/responses",
        "/backend-api/codex/responses",
        json_headers,
    )
    assert not _should_skip_trace_record(
        "https://npm.mycorp.internal:443/private-pkg",
        "/private-pkg",
        json_headers,
    )
    assert not _should_skip_trace_record(
        "https://api.anthropic.com:443/v1/messages",
        "/v1/messages",
        json_headers,
        npm_headers,
        "POST",
    )


@pytest.mark.asyncio
async def test_forward_proxy_unrecorded_response_closes_upstream_on_client_disconnect():
    from claude_tap.forward_proxy import ForwardProxyServer

    class FakeContent:
        async def iter_chunked(self, size):
            assert size == 65536
            yield b"package-bytes"

    class FakeResponse:
        status = 200
        reason = "OK"
        headers = {"Content-Type": "application/json"}
        content = FakeContent()

        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class DisconnectingWriter:
        def __init__(self) -> None:
            self.drain_calls = 0
            self.writes: list[bytes] = []

        def write(self, data: bytes) -> None:
            self.writes.append(data)

        async def drain(self) -> None:
            self.drain_calls += 1
            if self.drain_calls > 1:
                raise ConnectionError("client disconnected")

    upstream_resp = FakeResponse()
    writer = DisconnectingWriter()

    with pytest.raises(ConnectionError, match="client disconnected"):
        await ForwardProxyServer._relay_unrecorded_response(object(), upstream_resp, writer)

    assert upstream_resp.closed is True


@pytest.mark.asyncio
async def test_forward_proxy_connect():
    """Test the forward proxy CONNECT/TLS flow with a fake HTTPS upstream."""
    import ssl

    import aiohttp

    from claude_tap.certs import CertificateAuthority, ensure_ca
    from claude_tap.forward_proxy import ForwardProxyServer

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        ca_dir = tmpdir / "ca"

        # Generate CA
        ca_cert_path, ca_key_path = ensure_ca(ca_dir)
        ca = CertificateAuthority(ca_cert_path, ca_key_path)

        # Start a fake HTTPS upstream server
        upstream_port = await _start_fake_https_upstream(tmpdir)
        print(f"  Fake HTTPS upstream on port {upstream_port}")

        # Start forward proxy (disable SSL verify for the upstream session
        # since our fake upstream uses a self-signed cert)
        store, session_id, writer = _writer_for_dir(tmpdir)
        upstream_ssl_ctx = ssl.create_default_context()
        upstream_ssl_ctx.check_hostname = False
        upstream_ssl_ctx.verify_mode = ssl.CERT_NONE
        upstream_conn = aiohttp.TCPConnector(ssl=upstream_ssl_ctx)
        session = aiohttp.ClientSession(connector=upstream_conn, auto_decompress=False)

        server = ForwardProxyServer(
            host="127.0.0.1",
            port=0,
            ca=ca,
            writer=writer,
            session=session,
        )
        proxy_port = await server.start()
        print(f"  Forward proxy on port {proxy_port}")

        try:
            # Create an SSL context that trusts our CA
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.load_verify_locations(str(ca_cert_path))

            # Use aiohttp with our proxy to make an HTTPS request
            # We connect to 127.0.0.1:<upstream_port> through the proxy
            conn = aiohttp.TCPConnector(ssl=ssl_ctx)
            proxy_url = f"http://127.0.0.1:{proxy_port}"

            async with aiohttp.ClientSession(connector=conn, auto_decompress=False) as client:
                # Make request through the proxy to our fake upstream
                async with client.post(
                    f"https://127.0.0.1:{upstream_port}/v1/messages",
                    proxy=proxy_url,
                    json={
                        "model": "claude-test-model",
                        "max_tokens": 100,
                        "messages": [{"role": "user", "content": "hello"}],
                    },
                    headers={
                        "x-api-key": "sk-ant-test-key-12345678",
                        "anthropic-version": "2023-06-01",
                    },
                ) as resp:
                    assert resp.status == 200, f"Expected 200, got {resp.status}"
                    body = await resp.json()
                    assert body["content"][0]["text"] == "Hello from HTTPS!"
                    print("  OK: CONNECT + TLS termination works")

            # Allow trace to be written
            await asyncio.sleep(0.1)

            # Check trace was recorded
            writer.close()
            trace_text = store.export_jsonl(session_id).strip()
            assert trace_text, "No trace recorded"
            records = [json.loads(line) for line in trace_text.splitlines()]
            assert len(records) == 1
            assert records[0]["request"]["method"] == "POST"
            assert "/v1/messages" in records[0]["request"]["path"]
            assert records[0]["response"]["status"] == 200
            print("  OK: trace recorded correctly")

            # Check header redaction
            hdrs = {k.lower(): v for k, v in records[0]["request"]["headers"].items()}
            api_key = hdrs.get("x-api-key", "")
            assert api_key.endswith("..."), f"API key not redacted: {api_key}"
            print("  OK: API key redacted in trace")

        finally:
            await server.stop()
            await session.close()

    print("  test_forward_proxy_connect PASSED")


@pytest.mark.asyncio
async def test_forward_proxy_skips_package_noise_but_keeps_long_model_payloads(monkeypatch):
    """Real localhost proxy test for long npm noise and long model responses."""
    import ssl

    import aiohttp

    from claude_tap.certs import CertificateAuthority, ensure_ca
    from claude_tap.forward_proxy import ForwardProxyServer

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        ca_cert_path, ca_key_path = ensure_ca(tmpdir_path / "ca")
        ca = CertificateAuthority(ca_cert_path, ca_key_path)

        upstream_port, upstream_runner = await _start_fake_long_payload_https_upstream(tmpdir_path)
        store, session_id, writer = _writer_for_dir(tmpdir_path)

        upstream_ssl_ctx = ssl.create_default_context()
        upstream_ssl_ctx.check_hostname = False
        upstream_ssl_ctx.verify_mode = ssl.CERT_NONE
        upstream_conn = aiohttp.TCPConnector(ssl=upstream_ssl_ctx)
        session = aiohttp.ClientSession(connector=upstream_conn, auto_decompress=False)
        original_request = session.request

        async def _rewrite_to_localhost(method, url, **kwargs):
            rewritten = URL(str(url)).with_host("127.0.0.1").with_port(upstream_port)
            return await original_request(method=method, url=rewritten, **kwargs)

        monkeypatch.setattr(session, "request", _rewrite_to_localhost)

        server = ForwardProxyServer(
            host="127.0.0.1",
            port=0,
            ca=ca,
            writer=writer,
            session=session,
        )
        proxy_port = await server.start()

        try:
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.load_verify_locations(str(ca_cert_path))
            proxy_url = f"http://127.0.0.1:{proxy_port}"
            connector = aiohttp.TCPConnector(ssl=ssl_ctx)

            async with aiohttp.ClientSession(connector=connector, auto_decompress=False) as client:
                async with client.get(
                    f"https://registry.npmjs.org:{upstream_port}/effect",
                    proxy=proxy_url,
                ) as resp:
                    assert resp.status == 200
                    metadata = await resp.json()
                    assert len(metadata["versions"]) >= 100

                async with client.get(
                    f"https://cdn.example.test:{upstream_port}/effect/-/effect-4.0.0-beta.59.tgz",
                    proxy=proxy_url,
                ) as resp:
                    assert resp.status == 200
                    assert len(await resp.read()) == 1024 * 1024

                async with client.post(
                    f"https://api.anthropic.test:{upstream_port}/v1/messages",
                    proxy=proxy_url,
                    json={
                        "model": "claude-3-5-sonnet-test",
                        "max_tokens": 200000,
                        "messages": [{"role": "user", "content": "write a very long answer"}],
                    },
                ) as resp:
                    assert resp.status == 200
                    anthropic_body = await resp.json()
                    assert len(anthropic_body["content"][0]["text"]) > 200000

                async with client.post(
                    f"https://api.openai.test:{upstream_port}/v1/chat/completions",
                    proxy=proxy_url,
                    json={
                        "model": "gpt-5-test",
                        "messages": [{"role": "user", "content": "write a very long answer"}],
                    },
                ) as resp:
                    assert resp.status == 200
                    openai_body = await resp.json()
                    assert len(openai_body["choices"][0]["message"]["content"]) > 200000

            await asyncio.sleep(0.1)
            writer.close()
            records = [json.loads(line) for line in store.export_jsonl(session_id).splitlines()]

            paths = [record["request"]["path"] for record in records]
            assert paths == ["/v1/messages", "/v1/chat/completions"]
            assert all("effect" not in path for path in paths)

            anthropic_record = records[0]
            openai_record = records[1]
            assert anthropic_record["request"]["body"]["model"] == "claude-3-5-sonnet-test"
            assert len(anthropic_record["response"]["body"]["content"][0]["text"]) > 200000
            assert openai_record["request"]["body"]["model"] == "gpt-5-test"
            assert len(openai_record["response"]["body"]["choices"][0]["message"]["content"]) > 200000
        finally:
            await server.stop()
            await session.close()
            await upstream_runner.cleanup()


@pytest.mark.asyncio
async def test_forward_proxy_local_reverse_bridge():
    """Test local origin requests bridged through forward proxy to a target."""
    import ssl

    import aiohttp

    from claude_tap.certs import CertificateAuthority, ensure_ca
    from claude_tap.forward_proxy import ForwardProxyServer

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        ca_cert_path, ca_key_path = ensure_ca(tmpdir_path / "ca")
        ca = CertificateAuthority(ca_cert_path, ca_key_path)

        upstream_port = await _start_fake_https_upstream(tmpdir_path)
        store, session_id, writer = _writer_for_dir(tmpdir_path)
        upstream_ssl_ctx = ssl.create_default_context()
        upstream_ssl_ctx.check_hostname = False
        upstream_ssl_ctx.verify_mode = ssl.CERT_NONE
        upstream_conn = aiohttp.TCPConnector(ssl=upstream_ssl_ctx)
        session = aiohttp.ClientSession(connector=upstream_conn, auto_decompress=False)

        server = ForwardProxyServer(
            host="127.0.0.1",
            port=0,
            ca=ca,
            writer=writer,
            session=session,
            local_reverse_target=f"https://127.0.0.1:{upstream_port}",
            local_reverse_allowed_path_prefixes=("/v1internal",),
        )
        proxy_port = await server.start()

        try:

            async def chunked_body():
                yield b'{"request":{"contents":['
                yield b'{"role":"user","parts":[{"text":"hello"}]}'
                yield b"]}}"

            async with aiohttp.ClientSession(auto_decompress=False) as client:
                async with client.post(
                    f"http://127.0.0.1:{proxy_port}/v1internal:loadCodeAssist",
                    data=chunked_body(),
                    headers={"Content-Type": "application/json"},
                ) as resp:
                    assert resp.status == 200
                    body = await resp.json()
                    assert body["content"][0]["text"] == "Hello from HTTPS!"

            await asyncio.sleep(0.1)
            writer.close()
            records = [json.loads(line) for line in store.export_jsonl(session_id).splitlines()]
            assert len(records) == 1
            assert records[0]["request"]["path"] == "/v1internal:loadCodeAssist"
            assert records[0]["request"]["body"]["request"]["contents"][0]["role"] == "user"
            assert records[0]["response"]["status"] == 200
        finally:
            await server.stop()
            await session.close()

    print("  test_forward_proxy_local_reverse_bridge PASSED")


@pytest.mark.asyncio
async def test_forward_proxy_records_upstream_error():
    """Test forward proxy records a 502 trace record when upstream is unreachable."""
    import socket

    import aiohttp

    from claude_tap.certs import CertificateAuthority, ensure_ca
    from claude_tap.forward_proxy import ForwardProxyServer

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        ca_cert_path, ca_key_path = ensure_ca(tmpdir_path / "ca")
        ca = CertificateAuthority(ca_cert_path, ca_key_path)

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        unreachable_port = sock.getsockname()[1]
        sock.close()

        store, session_id, writer = _writer_for_dir(tmpdir_path)
        session = aiohttp.ClientSession(auto_decompress=False)
        server = ForwardProxyServer(
            host="127.0.0.1",
            port=0,
            ca=ca,
            writer=writer,
            session=session,
            local_reverse_target=f"http://127.0.0.1:{unreachable_port}",
            local_reverse_allowed_path_prefixes=("/v1internal",),
        )
        proxy_port = await server.start()

        try:
            async with aiohttp.ClientSession(auto_decompress=False) as client:
                async with client.post(
                    f"http://127.0.0.1:{proxy_port}/v1internal:listExperiments",
                    json={"request": {"client": "agy"}},
                ) as resp:
                    assert resp.status == 502
                    assert await resp.text()

            await asyncio.sleep(0.1)
            writer.close()
            records = [json.loads(line) for line in store.export_jsonl(session_id).splitlines()]
            assert len(records) == 1
            assert records[0]["turn"] == 1
            assert records[0]["request"]["path"] == "/v1internal:listExperiments"
            assert records[0]["request"]["body"]["request"]["client"] == "agy"
            assert records[0]["response"]["status"] == 502
            assert records[0]["response"]["body"]["error"]
        finally:
            await server.stop()
            await session.close()

    print("  test_forward_proxy_records_upstream_error PASSED")


@pytest.mark.asyncio
async def test_forward_proxy_connect_websocket():
    """Test the forward proxy CONNECT/TLS flow with a fake WSS upstream."""
    import ssl

    import aiohttp

    from claude_tap.certs import CertificateAuthority, ensure_ca
    from claude_tap.forward_proxy import ForwardProxyServer

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        ca_dir = tmpdir / "ca"

        ca_cert_path, ca_key_path = ensure_ca(ca_dir)
        ca = CertificateAuthority(ca_cert_path, ca_key_path)

        upstream_port = await _start_fake_wss_upstream(tmpdir)
        print(f"  Fake WSS upstream on port {upstream_port}")

        store, session_id, writer = _writer_for_dir(tmpdir)
        upstream_ssl_ctx = ssl.create_default_context()
        upstream_ssl_ctx.check_hostname = False
        upstream_ssl_ctx.verify_mode = ssl.CERT_NONE
        upstream_conn = aiohttp.TCPConnector(ssl=upstream_ssl_ctx)
        session = aiohttp.ClientSession(connector=upstream_conn, auto_decompress=False)

        server = ForwardProxyServer(
            host="127.0.0.1",
            port=0,
            ca=ca,
            writer=writer,
            session=session,
            store_stream_events=True,
        )
        proxy_port = await server.start()
        print(f"  Forward proxy on port {proxy_port}")

        try:
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.load_verify_locations(str(ca_cert_path))
            proxy_url = f"http://127.0.0.1:{proxy_port}"

            async with aiohttp.ClientSession(auto_decompress=False) as client:
                ws = await client.ws_connect(
                    f"https://127.0.0.1:{upstream_port}/v1/responses",
                    proxy=proxy_url,
                    ssl=ssl_ctx,
                )
                await ws.send_json({"model": "gpt-test", "input": "hello"})

                received = []
                binary_received = False
                while True:
                    msg = await asyncio.wait_for(ws.receive(), timeout=5)
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        received.append(json.loads(msg.data))
                    elif msg.type == aiohttp.WSMsgType.BINARY:
                        binary_received = msg.data == b"binary-over-wss"
                    elif msg.type in (
                        aiohttp.WSMsgType.CLOSE,
                        aiohttp.WSMsgType.CLOSING,
                        aiohttp.WSMsgType.CLOSED,
                    ):
                        break
                await ws.close()

            assert len(received) == 3
            assert binary_received
            assert received[0]["type"] == "response.created"
            assert received[-1]["type"] == "response.completed"
            print("  OK: CONNECT + WSS upgrade works")

            await asyncio.sleep(0.1)
            writer.close()

            trace_text = store.export_jsonl(session_id).strip()
            assert trace_text, "No WS trace recorded"
            records = [json.loads(line) for line in trace_text.splitlines()]
            assert len(records) == 1
            assert records[0]["transport"] == "websocket"
            assert records[0]["request"]["method"] == "WEBSOCKET"
            assert records[0]["request"]["path"] == "/v1/responses"
            assert records[0]["response"]["status"] == 101
            assert records[0]["response"]["body"]["status"] == "completed"
            assert records[0]["response"]["body"]["output"][0]["content"][0]["text"] == "Hello over WSS"
            assert records[0]["request"]["ws_events"][0]["model"] == "gpt-test"
            assert len(records[0]["response"]["ws_events"]) == 3
            print("  OK: WS trace recorded correctly")
        finally:
            await server.stop()
            await session.close()

    print("  test_forward_proxy_connect_websocket PASSED")


@pytest.mark.asyncio
async def test_forward_proxy_connect_websocket_honors_env_proxy(monkeypatch):
    """Forward proxy should pass env-derived proxy settings into upstream ws_connect."""
    import ssl

    import aiohttp

    from claude_tap.certs import CertificateAuthority, ensure_ca
    from claude_tap.forward_proxy import ForwardProxyServer

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        ca_dir = tmpdir / "ca"

        ca_cert_path, ca_key_path = ensure_ca(ca_dir)
        ca = CertificateAuthority(ca_cert_path, ca_key_path)

        upstream_port = await _start_fake_wss_upstream(tmpdir)
        store, session_id, writer = _writer_for_dir(tmpdir)
        upstream_ssl_ctx = ssl.create_default_context()
        upstream_ssl_ctx.check_hostname = False
        upstream_ssl_ctx.verify_mode = ssl.CERT_NONE
        upstream_conn = aiohttp.TCPConnector(ssl=upstream_ssl_ctx)
        session = aiohttp.ClientSession(connector=upstream_conn, auto_decompress=False, trust_env=True)

        monkeypatch.setattr(
            "claude_tap.forward_proxy._get_ws_proxy_settings",
            lambda _url: (URL("http://proxy.local:8080"), aiohttp.BasicAuth("user", "pass")),
        )

        ws_connect_calls: list[dict] = []
        original_ws_connect = session.ws_connect

        async def _spy_ws_connect(*args, **kwargs):
            ws_connect_calls.append(dict(kwargs))
            kwargs.pop("proxy", None)
            kwargs.pop("proxy_auth", None)
            return await original_ws_connect(*args, **kwargs)

        session.ws_connect = _spy_ws_connect  # type: ignore[method-assign]

        server = ForwardProxyServer(
            host="127.0.0.1",
            port=0,
            ca=ca,
            writer=writer,
            session=session,
        )
        proxy_port = await server.start()

        try:
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.load_verify_locations(str(ca_cert_path))

            async with aiohttp.ClientSession(auto_decompress=False) as client:
                ws = await client.ws_connect(
                    f"https://127.0.0.1:{upstream_port}/v1/responses",
                    proxy=f"http://127.0.0.1:{proxy_port}",
                    ssl=ssl_ctx,
                )
                await ws.send_json({"model": "gpt-test", "input": "hello"})
                while True:
                    msg = await asyncio.wait_for(ws.receive(), timeout=5)
                    if msg.type in (
                        aiohttp.WSMsgType.CLOSE,
                        aiohttp.WSMsgType.CLOSING,
                        aiohttp.WSMsgType.CLOSED,
                    ):
                        break
                await ws.close()

            assert ws_connect_calls, "Expected upstream ws_connect to be called"
            assert ws_connect_calls[0]["proxy"] == URL("http://proxy.local:8080")
            assert ws_connect_calls[0]["proxy_auth"] is not None
            assert ws_connect_calls[0]["proxy_auth"].login == "user"
            assert ws_connect_calls[0]["proxy_auth"].password == "pass"
        finally:
            await server.stop()
            await session.close()


async def _start_fake_https_upstream(tmpdir: Path) -> int:
    """Start a fake HTTPS server for testing. Returns the port."""
    import ssl as ssl_module

    # Generate a self-signed cert for the fake upstream
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("127.0.0.1"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        # Python 3.13/OpenSSL may enforce AKI/SKI presence for custom test certs.
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )

    cert_path = tmpdir / "upstream.pem"
    key_path = tmpdir / "upstream-key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )

    ssl_ctx = ssl_module.SSLContext(ssl_module.PROTOCOL_TLS_SERVER)
    ssl_ctx.load_cert_chain(str(cert_path), str(key_path))

    async def handle_client(reader, writer):
        try:
            await asyncio.wait_for(reader.readline(), timeout=10)
            # Read headers
            headers = {}
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=5)
                if line in (b"\r\n", b"\n", b""):
                    break
                decoded = line.decode("utf-8", errors="replace").strip()
                if ":" in decoded:
                    k, v = decoded.split(":", 1)
                    headers[k.strip()] = v.strip()

            # Read body (drain it so the connection is clean)
            cl = headers.get("Content-Length") or headers.get("content-length", "0")
            try:
                length = int(cl)
                if length > 0:
                    await asyncio.wait_for(reader.readexactly(length), timeout=10)
            except (ValueError, asyncio.IncompleteReadError):
                pass

            # Return a simple JSON response
            resp_body = json.dumps(
                {
                    "id": "msg_test_1",
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Hello from HTTPS!"}],
                    "model": "claude-test-model",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                    "stop_reason": "end_turn",
                }
            ).encode()

            content_length_line = f"Content-Length: {len(resp_body)}\r\n".encode()
            response = (
                b"HTTP/1.1 200 OK\r\n"
                + b"Content-Type: application/json\r\n"
                + content_length_line
                + b"\r\n"
                + resp_body
            )
            writer.write(response)
            await writer.drain()
        except Exception:
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    server = await asyncio.start_server(handle_client, "127.0.0.1", 0, ssl=ssl_ctx)
    port = server.sockets[0].getsockname()[1]
    return port


async def _start_fake_long_payload_https_upstream(tmpdir: Path):
    """Start a fake HTTPS upstream with package noise and long model payloads."""
    import datetime
    import ssl as ssl_module

    from aiohttp import web
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("127.0.0.1"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )

    cert_path = tmpdir / "long-upstream.pem"
    key_path = tmpdir / "long-upstream-key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )

    ssl_ctx = ssl_module.SSLContext(ssl_module.PROTOCOL_TLS_SERVER)
    ssl_ctx.load_cert_chain(str(cert_path), str(key_path))

    async def long_payload_handler(request):
        if request.path == "/effect":
            package_padding = "npm-metadata-padding-" * 1000
            versions = {
                f"4.0.0-beta.{index}": {
                    "name": "effect",
                    "dist": {"tarball": f"https://registry.npmjs.org/effect/-/effect-{index}.tgz"},
                    "readme": package_padding,
                }
                for index in range(120)
            }
            return web.json_response({"name": "effect", "versions": versions})

        if request.path.endswith(".tgz"):
            return web.Response(body=b"x" * 1024 * 1024, content_type="application/octet-stream")

        if request.path == "/v1/messages":
            req_body = await request.json()
            long_text = "anthropic-long-response " * 10000
            return web.json_response(
                {
                    "id": "msg_long_1",
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": long_text}],
                    "model": req_body["model"],
                    "usage": {
                        "input_tokens": 120000,
                        "output_tokens": 50000,
                        "cache_read_input_tokens": 90000,
                    },
                    "stop_reason": "end_turn",
                }
            )

        if request.path == "/v1/chat/completions":
            req_body = await request.json()
            long_text = "openai-long-response " * 11000
            return web.json_response(
                {
                    "id": "chatcmpl_long_1",
                    "object": "chat.completion",
                    "model": req_body["model"],
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": long_text}}],
                    "usage": {
                        "prompt_tokens": 130000,
                        "completion_tokens": 55000,
                        "prompt_tokens_details": {"cached_tokens": 95000},
                    },
                }
            )

        return web.Response(status=404, text="not found")

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", long_payload_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=ssl_ctx)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return port, runner


async def _start_fake_wss_upstream(tmpdir: Path) -> int:
    """Start a fake WSS upstream server for websocket proxy tests."""
    import ssl as ssl_module

    import aiohttp
    from aiohttp import web
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("127.0.0.1"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )

    cert_path = tmpdir / "upstream-ws.pem"
    key_path = tmpdir / "upstream-ws-key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )

    ssl_ctx = ssl_module.SSLContext(ssl_module.PROTOCOL_TLS_SERVER)
    ssl_ctx.load_cert_chain(str(cert_path), str(key_path))

    async def ws_handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)
                model = data.get("model", "gpt-test")
                await ws.send_json(
                    {
                        "type": "response.created",
                        "response": {"id": "resp_ws_1", "model": model, "status": "in_progress"},
                    }
                )
                await ws.send_bytes(b"binary-over-wss")
                await ws.send_json({"type": "response.output_text.delta", "delta": "Hello over WSS"})
                await ws.send_json(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp_ws_1",
                            "model": model,
                            "status": "completed",
                            "output": [
                                {
                                    "type": "message",
                                    "content": [{"type": "output_text", "text": "Hello over WSS"}],
                                }
                            ],
                            "usage": {"input_tokens": 10, "output_tokens": 5},
                        },
                    }
                )
                await ws.close()
                break
        return ws

    app = web.Application()
    app.router.add_get("/v1/responses", ws_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=ssl_ctx)
    await site.start()
    return site._server.sockets[0].getsockname()[1]


## ---------------------------------------------------------------------------
## Run all tests
## ---------------------------------------------------------------------------

if __name__ == "__main__":
    if "--preview" in sys.argv:
        _cmd_preview()
        sys.exit(0)
    if "--dev" in sys.argv:
        _cmd_dev()
        sys.exit(0)

    # Unit tests (fast, no subprocesses)
    test_parse_args()
    test_parse_args_new_flags()
    test_parse_args_proxy_mode()
    test_cert_generation()
    test_filter_headers()
    test_sse_reassembler()
    test_version_tuple()
    test_detect_installer()
    test_trace_cleanup()
    test_trace_tagging_safety()
    test_manifest_migration()
    test_live_viewer_scroll_preservation()
    test_live_viewer_diff_nav_update()

    # E2E tests (subprocess-based)
    test_e2e()
    test_upstream_error()
    test_malformed_sse()
    test_large_payload()
    test_concurrent_requests()
    test_upstream_unreachable()
    test_version_check_with_fake_pypi()
    test_version_check_no_update()
    test_e2e_with_cleanup()
    print("\n" + "=" * 60)
    print("  ALL TESTS PASSED")
    print("=" * 60)


## ---------------------------------------------------------------------------
## LiveViewerServer tests
## ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_viewer_server():
    """Test LiveViewerServer SSE functionality."""
    import aiohttp

    from claude_tap import LiveViewerServer

    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["CLOUDTAP_DB"] = str(Path(tmpdir) / "live.sqlite3")
        from claude_tap.trace_store import get_trace_store, reset_trace_store

        reset_trace_store()
        session_id = get_trace_store().create_session()
        server = LiveViewerServer(session_id=session_id, port=0)
        port = await server.start()
        assert port > 0
        print(f"  LiveViewerServer started on port {port}")

        async with aiohttp.ClientSession() as session:
            # Test index page
            async with session.get(f"http://127.0.0.1:{port}/") as resp:
                assert resp.status == 200
                html = await resp.text()
                assert "LIVE_MODE = true" in html
                print("  OK: index returns live mode HTML")

            # Test records endpoint (empty initially)
            async with session.get(f"http://127.0.0.1:{port}/records") as resp:
                assert resp.status == 200
                records = await resp.json()
                assert records == []
                print("  OK: /records returns empty list")

            # Broadcast a record
            test_record = {"turn": 1, "request": {"method": "POST"}}
            await server.broadcast(test_record)

            # Verify record is stored
            async with session.get(f"http://127.0.0.1:{port}/records") as resp:
                records = await resp.json()
                assert len(records) == 1
                assert records[0]["turn"] == 1
                print("  OK: broadcast adds record to /records")

        await server.stop()
        print("  test_live_viewer_server PASSED")


@pytest.mark.asyncio
async def test_dashboard_main_serves_viewer(monkeypatch, tmp_path):
    """Test the dashboard command starts a standalone dashboard server."""
    import socket
    from unittest.mock import AsyncMock

    import aiohttp

    from claude_tap import dashboard_main, parse_dashboard_args

    opened_urls: list[str] = []
    monkeypatch.setattr("claude_tap.cli._open_browser", opened_urls.append)
    monkeypatch.setenv("CLOUDTAP_DB", str(tmp_path / "dashboard.sqlite3"))
    monkeypatch.setattr("claude_tap.cli.is_dashboard_healthy", AsyncMock(return_value=False))
    monkeypatch.setattr(
        "claude_tap.cli.migrate_legacy_traces",
        lambda _output_dir: (_ for _ in ()).throw(AssertionError("dashboard_main should not pre-migrate")),
    )

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        dashboard_port = sock.getsockname()[1]

    args = parse_dashboard_args(["--tap-output-dir", str(tmp_path), "--tap-live-port", str(dashboard_port)])
    task = asyncio.create_task(dashboard_main(args))
    try:
        for _ in range(50):
            if opened_urls:
                break
            await asyncio.sleep(0.05)
        assert opened_urls, "dashboard should open the browser"
        async with aiohttp.ClientSession() as session:
            async with session.get(opened_urls[0]) as resp:
                assert resp.status == 200
                html = await resp.text()
                assert "session-list" in html
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_dashboard_main_opens_reused_dashboard(monkeypatch, tmp_path):
    """The standalone dashboard command should honor browser opens when reusing a server."""
    from unittest.mock import AsyncMock

    from claude_tap import dashboard_main, parse_dashboard_args

    opened_urls: list[str] = []
    migration_calls: list[Path] = []
    monkeypatch.setattr("claude_tap.cli._open_browser", opened_urls.append)
    monkeypatch.setenv("CLOUDTAP_DB", str(tmp_path / "dashboard.sqlite3"))
    monkeypatch.setattr("claude_tap.cli.is_dashboard_healthy", AsyncMock(return_value=True))
    monkeypatch.setattr("claude_tap.cli.migrate_legacy_traces", migration_calls.append)

    args = parse_dashboard_args(["--tap-output-dir", str(tmp_path), "--tap-live-port", "23456"])

    assert await dashboard_main(args) == 0
    assert opened_urls == ["http://127.0.0.1:23456"]
    assert migration_calls == [tmp_path]
