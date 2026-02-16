#!/usr/bin/env python3
"""POC: Interactive CLI testing with pexpect.

This demonstrates how to test an interactive CLI tool like claude-tap
by simulating multi-turn conversations.

Strategy:
1. Fake interactive claude CLI that waits for user input
2. Fake upstream API server that returns mock responses (non-streaming for simplicity)
3. pexpect to drive the interaction
4. Assertions on generated JSONL/HTML files
"""

import asyncio
import json
import os
import stat
import sys
import tempfile
import threading
import time
from pathlib import Path

import pexpect

# ---------------------------------------------------------------------------
# Fake Interactive Claude CLI (non-streaming for simpler testing)
# ---------------------------------------------------------------------------

FAKE_INTERACTIVE_CLAUDE = r'''#!/usr/bin/env python3
"""Fake interactive claude CLI — simulates multi-turn conversation (non-streaming)."""
import json
import os
import sys
import urllib.request

base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
url = f"{base}/v1/messages"

conversation_history = []

def send_request(user_input: str) -> str:
    """Send a non-streaming request to the API and return the response."""
    conversation_history.append({"role": "user", "content": user_input})

    req_body = json.dumps({
        "model": "claude-opus-4-6",
        "max_tokens": 1000,
        "stream": False,  # Non-streaming for simplicity
        "messages": conversation_history,
    }).encode()

    req = urllib.request.Request(url, data=req_body, headers={
        "Content-Type": "application/json",
        "x-api-key": "sk-ant-test-key",
        "anthropic-version": "2023-06-01",
    })

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
            body = json.loads(data)
            text = body.get("content", [{}])[0].get("text", "No response")
            conversation_history.append({"role": "assistant", "content": text})
            return text
    except Exception as e:
        return f"Error: {e}"

def main():
    print("Welcome to Fake Claude! Type 'exit' to quit.")
    print()

    turn = 0
    while True:
        try:
            sys.stdout.write("You: ")
            sys.stdout.flush()

            user_input = input().strip()

            if user_input.lower() in ("exit", "quit", "/exit", "/quit"):
                print("Goodbye!")
                break

            if not user_input:
                continue

            turn += 1
            print(f"\nAssistant (Turn {turn}):")
            response = send_request(user_input)
            print(response)
            print()

        except EOFError:
            print("\nGoodbye!")
            break
        except KeyboardInterrupt:
            print("\nInterrupted!")
            break

if __name__ == "__main__":
    main()
'''


# ---------------------------------------------------------------------------
# Fake Upstream API Server (non-streaming)
# ---------------------------------------------------------------------------


def run_fake_upstream_in_thread(port: int):
    """Run fake upstream server in a background thread."""
    from aiohttp import web

    ready = threading.Event()
    request_count = [0]
    requests_log = []  # Store all requests for verification
    loop = None
    runner = None

    async def handle_messages(request):
        """Handle /v1/messages endpoint."""
        body = await request.read()
        req = json.loads(body) if body else {}

        request_count[0] += 1
        requests_log.append(req)

        # Get user message
        messages = req.get("messages", [])
        user_msg = messages[-1].get("content", "") if messages else ""

        # Generate mock response based on input
        if "hello" in user_msg.lower():
            response_text = "Hello! How can I help you today?"
        elif "fibonacci" in user_msg.lower():
            response_text = "def fib(n):\n    if n <= 1: return n\n    return fib(n-1) + fib(n-2)"
        elif "count" in user_msg.lower():
            response_text = "1, 2, 3, 4, 5!"
        else:
            response_text = f"I received: '{user_msg}'. This is turn {request_count[0]}."

        # Return Anthropic-compatible response
        return web.json_response(
            {
                "id": f"msg_test_{request_count[0]}",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": response_text}],
                "model": "claude-opus-4-6",
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 100 + len(user_msg),
                    "output_tokens": len(response_text.split()),
                },
            }
        )

    app = web.Application()
    app.router.add_post("/v1/messages", handle_messages)

    def run():
        nonlocal loop, runner
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def start():
            nonlocal runner
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", port)
            await site.start()
            ready.set()
            while True:
                await asyncio.sleep(1)

        try:
            loop.run_until_complete(start())
        except asyncio.CancelledError:
            pass

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    ready.wait(timeout=5)

    def cleanup():
        if loop:
            loop.call_soon_threadsafe(loop.stop)

    return cleanup, request_count, requests_log


# ---------------------------------------------------------------------------
# Main test
# ---------------------------------------------------------------------------


def run_interactive_test():
    """Run the interactive CLI test."""
    print("=" * 60)
    print("POC: Interactive CLI Testing with pexpect")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # Create fake claude
        fake_claude = tmp_path / "claude"
        fake_claude.write_text(FAKE_INTERACTIVE_CLAUDE)
        fake_claude.chmod(fake_claude.stat().st_mode | stat.S_IXUSR)

        # Start fake upstream
        upstream_port = 19499
        cleanup, request_count, requests_log = run_fake_upstream_in_thread(upstream_port)

        # Wait for server to be ready
        time.sleep(0.5)

        try:
            env = os.environ.copy()
            env["PATH"] = f"{tmp_path}:{env.get('PATH', '')}"

            claude_tap_dir = Path(__file__).parent.parent
            upstream_url = f"http://127.0.0.1:{upstream_port}"
            # Use absolute path for traces directory
            traces_dir = tmp_path / "traces"
            traces_dir.mkdir(parents=True, exist_ok=True)

            print(f"\n📁 Temp dir: {tmp_path}")
            print(f"🔌 Upstream: {upstream_url}")
            print(f"📦 claude-tap: {claude_tap_dir}")

            # Run with pexpect
            print("\n🚀 Starting interactive test...\n")

            child = pexpect.spawn(
                sys.executable,
                ["-m", "claude_tap", "--tap-output-dir", str(traces_dir), "--tap-target", upstream_url],
                cwd=str(claude_tap_dir),
                env=env,
                timeout=30,
                encoding="utf-8",
            )

            # Enable logging
            child.logfile = sys.stdout

            # Wait for startup
            child.expect(r"listening on http", timeout=10)
            print("\n✅ Proxy started")

            child.expect(r"Welcome to Fake Claude", timeout=10)
            print("✅ Fake claude started")

            # Turn 1: Hello
            child.expect(r"You: ", timeout=5)
            child.sendline("hello world")
            child.expect(r"Hello!", timeout=15)
            print("\n✅ Turn 1 complete")

            # Turn 2: Code request
            child.expect(r"You: ", timeout=5)
            child.sendline("write fibonacci code")
            child.expect(r"def fib", timeout=15)
            print("✅ Turn 2 complete")

            # Turn 3: Count
            child.expect(r"You: ", timeout=5)
            child.sendline("count to five")
            child.expect(r"1.*2.*3", timeout=15)
            print("✅ Turn 3 complete")

            # Exit
            child.expect(r"You: ", timeout=5)
            child.sendline("exit")
            child.expect(pexpect.EOF, timeout=10)
            print("✅ Exited cleanly")

            child.close()

            # ===== ASSERTIONS =====
            print("\n" + "=" * 60)
            print("📊 Verification")
            print("=" * 60)

            # Check request count
            assert request_count[0] >= 3, f"Expected at least 3 requests, got {request_count[0]}"
            print(f"✅ API requests: {request_count[0]}")

            # Check traces directory
            assert traces_dir.exists(), "Traces directory should exist"
            print(f"✅ Traces dir exists: {traces_dir}")

            # Check JSONL file
            jsonl_files = list(traces_dir.glob("*.jsonl"))
            assert len(jsonl_files) > 0, "Should have JSONL trace file"
            print(f"✅ JSONL file: {jsonl_files[0].name}")

            # Parse and verify JSONL content
            jsonl_file = jsonl_files[0]
            traces = [json.loads(line) for line in jsonl_file.read_text().splitlines() if line.strip()]
            assert len(traces) >= 3, f"Expected at least 3 traces, got {len(traces)}"
            print(f"✅ Traces recorded: {len(traces)}")

            # Verify trace structure
            for i, trace in enumerate(traces):
                assert "turn" in trace, f"Trace {i} missing 'turn'"
                assert "request" in trace, f"Trace {i} missing 'request'"
                assert "response" in trace, f"Trace {i} missing 'response'"
            print("✅ Trace structure valid")

            # Check HTML file
            html_files = list(traces_dir.glob("*.html"))
            assert len(html_files) > 0, "Should have HTML viewer file"
            html_content = html_files[0].read_text()
            assert "Claude Trace" in html_content or "claude-tap" in html_content.lower(), "HTML should be viewer"
            print(f"✅ HTML viewer: {html_files[0].name}")

            # Verify requests contain expected content
            assert any("hello" in str(r).lower() for r in requests_log), "Should have hello request"
            assert any("fibonacci" in str(r).lower() for r in requests_log), "Should have fibonacci request"
            print("✅ Request content verified")

            print("\n" + "=" * 60)
            print("🎉 ALL TESTS PASSED!")
            print("=" * 60)

            return True

        except Exception as e:
            print(f"\n❌ Test failed: {e}")
            import traceback

            traceback.print_exc()
            return False

        finally:
            cleanup()


if __name__ == "__main__":
    success = run_interactive_test()
    sys.exit(0 if success else 1)
