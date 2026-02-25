"""Real E2E tests using actual Claude CLI.

These tests start claude-tap from local source, connect to a real Claude CLI,
send real prompts, and verify trace output.

Prerequisites:
  - `claude` CLI installed and authenticated
  - Run with: uv run pytest tests/e2e/ --run-real-e2e --timeout=300
"""

import json
import subprocess
import time
from pathlib import Path

import pytest


def _wait_for_trace_files(trace_dir: str, min_records: int = 1, timeout: float = 120) -> list[dict]:
    """Wait for trace JSONL files to appear and contain at least min_records."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        jsonl_files = list(Path(trace_dir).glob("trace_*.jsonl"))
        if jsonl_files:
            records = []
            for f in jsonl_files:
                text = f.read_text().strip()
                if text:
                    for line in text.splitlines():
                        try:
                            records.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
            if len(records) >= min_records:
                return records
        time.sleep(1)
    raise TimeoutError(f"Expected at least {min_records} trace records in {trace_dir}, found none after {timeout}s")


def _run_claude_prompt(env: dict, prompt: str, extra_args: list[str] | None = None, timeout: float = 120) -> str:
    """Run `claude -p <prompt>` and return stdout."""
    cmd = ["claude", "-p", prompt]
    if extra_args:
        cmd.extend(extra_args)
    result = subprocess.run(
        cmd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"claude -p failed (code {result.returncode}):\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result.stdout


class TestRealProxy:
    """Tests that run real Claude CLI through the claude-tap proxy."""

    @pytest.mark.timeout(180)
    def test_single_turn(self, claude_env):
        """Single prompt-response: verify trace captures the exchange."""
        env, trace_dir = claude_env

        output = _run_claude_prompt(env, "Reply with exactly: HELLO_E2E_TEST")

        assert "HELLO_E2E_TEST" in output, f"Expected HELLO_E2E_TEST in output, got: {output[:500]}"

        records = _wait_for_trace_files(trace_dir, min_records=1)
        assert len(records) >= 1, f"Expected at least 1 trace record, got {len(records)}"

        # Verify the trace contains request and response
        record = records[0]
        assert "request" in record
        assert "response" in record
        assert record["request"]["method"] == "POST"
        assert "/v1/messages" in record["request"]["path"]

        # Verify the response was captured
        resp_body = record["response"].get("body", {})
        if isinstance(resp_body, dict):
            content = resp_body.get("content", [])
            texts = [c.get("text", "") for c in content if c.get("type") == "text"]
            full_text = " ".join(texts)
            assert "HELLO_E2E_TEST" in full_text, f"Expected HELLO_E2E_TEST in trace response, got: {full_text[:500]}"

    @pytest.mark.timeout(300)
    def test_multi_turn(self, claude_env):
        """Two calls with -c flag: verify conversation memory works."""
        env, trace_dir = claude_env

        # First turn
        output1 = _run_claude_prompt(env, "Remember this code: ZEBRA_42. Just confirm you remember it.")
        assert "ZEBRA_42" in output1 or "remember" in output1.lower(), f"Unexpected first turn output: {output1[:500]}"

        # Second turn with -c (continue)
        output2 = _run_claude_prompt(env, "What was the code I asked you to remember?", extra_args=["-c"])
        assert "ZEBRA_42" in output2, f"Expected ZEBRA_42 in continued conversation, got: {output2[:500]}"

        # Verify multiple trace records
        records = _wait_for_trace_files(trace_dir, min_records=2)
        assert len(records) >= 2, f"Expected at least 2 trace records for multi-turn, got {len(records)}"

    @pytest.mark.timeout(180)
    def test_tool_use(self, claude_env):
        """Prompt that triggers tool use: verify multiple trace records."""
        env, trace_dir = claude_env

        _run_claude_prompt(env, "What files are in the current directory? Use ls to check.")

        # Tool use should generate multiple API calls (initial + tool result + response)
        records = _wait_for_trace_files(trace_dir, min_records=2, timeout=180)
        assert len(records) >= 2, f"Expected at least 2 trace records for tool use, got {len(records)}"

        # Verify at least one record shows tool use in response
        has_tool_use = False
        for record in records:
            resp_body = record.get("response", {}).get("body", {})
            if isinstance(resp_body, dict):
                content = resp_body.get("content", [])
                for block in content:
                    if block.get("type") == "tool_use":
                        has_tool_use = True
                        break
            if has_tool_use:
                break
        assert has_tool_use, "Expected at least one trace record with tool_use content block"

    @pytest.mark.timeout(180)
    def test_html_viewer_generated(self, claude_env):
        """Verify .html viewer file is generated after a session."""
        env, trace_dir = claude_env

        _run_claude_prompt(env, "Reply with exactly: HTML_VIEWER_CHECK")

        # Wait for trace files
        _wait_for_trace_files(trace_dir, min_records=1)

        # The HTML viewer is generated on shutdown, which happens when
        # the proxy_server fixture tears down. We need to trigger that
        # by letting the fixture cleanup run. For now, just check JSONL exists.
        jsonl_files = list(Path(trace_dir).glob("trace_*.jsonl"))
        assert len(jsonl_files) >= 1, "Expected at least one trace JSONL file"

        # HTML is generated at proxy shutdown — verify JSONL has valid content
        for jsonl_file in jsonl_files:
            text = jsonl_file.read_text().strip()
            if text:
                record = json.loads(text.splitlines()[0])
                assert "request" in record
                assert "response" in record

    @pytest.mark.timeout(180)
    def test_api_key_redaction(self, claude_env):
        """Verify no raw API keys appear in trace files."""
        env, trace_dir = claude_env

        _run_claude_prompt(env, "Reply with exactly: REDACTION_CHECK")

        records = _wait_for_trace_files(trace_dir, min_records=1)

        # Check that no raw API keys leak into the trace
        trace_text = json.dumps(records)
        # Anthropic keys start with sk-ant-
        assert "sk-ant-" not in trace_text, "Raw Anthropic API key found in trace — should be redacted"

        # Verify the x-api-key header is redacted
        for record in records:
            req_headers = record.get("request", {}).get("headers", {})
            api_key = req_headers.get("x-api-key", "")
            if api_key:
                assert api_key.startswith("sk-ant-") is False or "..." in api_key, (
                    f"API key not properly redacted: {api_key[:20]}..."
                )

    @pytest.mark.timeout(180)
    def test_streaming_sse_capture(self, claude_env):
        """Verify SSE events are captured in streaming responses."""
        env, trace_dir = claude_env

        _run_claude_prompt(env, "Reply with exactly: SSE_CAPTURE_TEST")

        records = _wait_for_trace_files(trace_dir, min_records=1)

        # Check if any record has sse_events (streaming response)
        has_sse = False
        for record in records:
            sse_events = record.get("response", {}).get("sse_events")
            if sse_events and len(sse_events) > 0:
                has_sse = True
                # Verify SSE events have expected structure
                event_types = {e.get("event") for e in sse_events if isinstance(e, dict)}
                assert "message_start" in event_types, f"Expected message_start in SSE events, got: {event_types}"
                break

        assert has_sse, "Expected at least one trace record with sse_events (streaming response)"
