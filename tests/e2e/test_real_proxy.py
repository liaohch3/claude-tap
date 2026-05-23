"""Real E2E tests using actual Claude CLI.

These tests run claude-tap as a subprocess and execute the real `claude` CLI.
Proxy mode is selected by fixture config:
  - reverse mode uses ANTHROPIC_BASE_URL (recommended when ANTHROPIC_API_KEY is set)
  - forward mode uses HTTPS_PROXY + CONNECT/TLS MITM (OAuth-compatible in principle)

Prerequisites:
  - `claude` CLI installed and authenticated
  - For reverse mode: set ANTHROPIC_API_KEY
  - Run with: uv run --extra dev pytest tests/e2e/ --run-real-e2e -v --timeout=300
"""

import subprocess
import sys
from pathlib import Path

import pytest


def _run_claude_tap(
    env: dict,
    trace_dir: str,
    prompt: str,
    proxy_mode: str = "forward",
    extra_claude_args: list[str] | None = None,
    timeout: float = 120,
) -> subprocess.CompletedProcess:
    """Run claude-tap wrapping `claude -p <prompt>` with the selected mode."""
    cmd = [
        sys.executable,
        "-m",
        "claude_tap",
        "--tap-output-dir",
        trace_dir,
        "--tap-no-update-check",
        "--tap-proxy-mode",
        proxy_mode,
        "--",  # separator: everything after goes to claude
        "-p",
        prompt,
    ]
    if extra_claude_args:
        cmd.extend(extra_claude_args)

    result = subprocess.run(
        cmd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result


def _read_trace_records(trace_dir: str) -> list[dict]:
    """Read all trace records from the SQLite database."""
    from claude_tap.trace_store import get_trace_store

    store = get_trace_store()
    records = []
    for row in store.list_session_rows():
        records.extend(store.load_records(row["id"]))
    return records


class TestRealProxy:
    """Tests that run real Claude CLI through the claude-tap proxy."""

    @pytest.mark.timeout(180)
    def test_single_turn(self, claude_env):
        """Single prompt-response: verify trace captures the exchange."""
        env, trace_dir, proxy_mode = claude_env

        result = _run_claude_tap(env, trace_dir, "Reply with exactly: HELLO_E2E_TEST", proxy_mode=proxy_mode)

        assert result.returncode == 0, (
            f"claude-tap failed (code {result.returncode}):\n"
            f"stdout: {result.stdout[:1000]}\nstderr: {result.stderr[:1000]}"
        )
        assert "HELLO_E2E_TEST" in result.stdout, f"Expected HELLO_E2E_TEST in output:\n{result.stdout[:500]}"

        records = _read_trace_records(trace_dir)
        assert len(records) >= 1, f"Expected at least 1 trace record, got {len(records)}"

        # OAuth/SDK may issue several preflight calls before /v1/messages.
        msg_records = [r for r in records if "/v1/messages" in r.get("request", {}).get("path", "")]
        assert msg_records, "Expected at least one /v1/messages trace record"

        # Verify trace structure on a messages call
        record = msg_records[0]
        assert "request" in record
        assert "response" in record
        assert record["request"]["method"] == "POST"

        # Verify response content captured from any messages response
        found_expected_text = False
        for rec in msg_records:
            resp_body = rec.get("response", {}).get("body", {})
            if not isinstance(resp_body, dict):
                continue
            content = resp_body.get("content", [])
            texts = [c.get("text", "") for c in content if c.get("type") == "text"]
            full_text = " ".join(texts)
            if "HELLO_E2E_TEST" in full_text:
                found_expected_text = True
                break
        assert found_expected_text, "Expected HELLO_E2E_TEST in at least one /v1/messages trace response"

    @pytest.mark.timeout(300)
    def test_multi_turn(self, claude_env):
        """Two calls with -c flag: verify conversation memory works."""
        env, trace_dir, proxy_mode = claude_env

        # Turn 1: ask to remember a code
        r1 = _run_claude_tap(
            env, trace_dir, "Remember this code: ZEBRA_42. Just confirm you remember it.", proxy_mode=proxy_mode
        )
        assert r1.returncode == 0, f"Turn 1 failed:\nstdout: {r1.stdout[:500]}\nstderr: {r1.stderr[:500]}"

        # Turn 2: with -c (continue) ask to recall
        r2 = _run_claude_tap(
            env,
            trace_dir,
            "What was the code I asked you to remember?",
            proxy_mode=proxy_mode,
            extra_claude_args=["-c"],
        )
        assert r2.returncode == 0, f"Turn 2 failed:\nstdout: {r2.stdout[:500]}\nstderr: {r2.stderr[:500]}"
        assert "ZEBRA_42" in r2.stdout, f"Expected ZEBRA_42 in continued conversation:\n{r2.stdout[:500]}"

        # Verify multiple trace records across both runs
        records = _read_trace_records(trace_dir)
        assert len(records) >= 2, f"Expected at least 2 trace records for multi-turn, got {len(records)}"

    @pytest.mark.timeout(180)
    def test_tool_use(self, claude_env):
        """Prompt that triggers tool use: verify trace captures tool_use blocks."""
        env, trace_dir, proxy_mode = claude_env

        result = _run_claude_tap(
            env, trace_dir, "What files are in the current directory? Use ls to check.", proxy_mode=proxy_mode
        )
        assert result.returncode == 0, f"Tool use test failed:\n{result.stdout[:500]}\n{result.stderr[:500]}"

        records = _read_trace_records(trace_dir)
        # Tool use generates multiple API calls (initial + tool result + response)
        assert len(records) >= 2, f"Expected at least 2 trace records for tool use, got {len(records)}"

        # Verify at least one record has tool_use content block
        has_tool_use = False
        for record in records:
            resp_body = record.get("response", {}).get("body", {})
            if isinstance(resp_body, dict):
                for block in resp_body.get("content", []):
                    if block.get("type") == "tool_use":
                        has_tool_use = True
                        break
            if has_tool_use:
                break
        assert has_tool_use, "Expected at least one trace record with tool_use content block"

    @pytest.mark.timeout(180)
    def test_html_viewer_generated(self, claude_env):
        """Verify .html viewer file is generated after a session."""
        env, trace_dir, proxy_mode = claude_env

        result = _run_claude_tap(env, trace_dir, "Reply with exactly: HTML_CHECK", proxy_mode=proxy_mode)
        assert result.returncode == 0

        # Query database to get session ID
        from claude_tap.trace_store import get_trace_store

        store = get_trace_store()
        session_rows = store.list_session_rows()
        assert len(session_rows) >= 1
        session_id = session_rows[0]["id"]

        # Explicitly run the export command to generate the HTML viewer file
        cmd = [
            sys.executable,
            "-m",
            "claude_tap",
            "export",
            "--session-id",
            session_id,
            "-o",
            str(Path(trace_dir) / "viewer.html"),
        ]
        export_result = subprocess.run(cmd, env=env, capture_output=True, text=True)
        assert export_result.returncode == 0

        html_files = list(Path(trace_dir).glob("*.html"))
        assert len(html_files) >= 1, (
            f"Expected HTML viewer file in {trace_dir}, found: {list(Path(trace_dir).iterdir())}"
        )

        # Verify HTML contains embedded trace data
        html_content = html_files[0].read_text()
        assert "EMBEDDED_TRACE_DATA" in html_content, "HTML viewer should contain EMBEDDED_TRACE_DATA"

        # Verify Dashboard: line in stdout
        assert "Dashboard:" in result.stdout, "Expected 'Dashboard:' URL in stdout"

    @pytest.mark.timeout(180)
    def test_api_key_redaction(self, claude_env):
        """Verify no raw API keys appear in trace files."""
        env, trace_dir, proxy_mode = claude_env

        result = _run_claude_tap(env, trace_dir, "Reply with exactly: REDACTION_CHECK", proxy_mode=proxy_mode)
        assert result.returncode == 0

        records = _read_trace_records(trace_dir)
        assert len(records) >= 1

        # Check all records for raw API keys in headers
        for record in records:
            req_headers = record.get("request", {}).get("headers", {})
            for key_name in ("x-api-key", "authorization"):
                val = req_headers.get(key_name, "")
                if not val:
                    # Try case-insensitive
                    for k, v in req_headers.items():
                        if k.lower() == key_name:
                            val = v
                            break
                if val and len(val) > 10:
                    assert "..." in val, f"Header {key_name} not redacted: {val[:30]}..."

    @pytest.mark.timeout(180)
    def test_streaming_sse_capture(self, claude_env):
        """Verify SSE events are captured in streaming responses."""
        env, trace_dir, proxy_mode = claude_env

        result = _run_claude_tap(env, trace_dir, "Reply with exactly: SSE_CAPTURE_TEST", proxy_mode=proxy_mode)
        assert result.returncode == 0

        records = _read_trace_records(trace_dir)
        assert len(records) >= 1

        # Check if any record has sse_events (streaming response)
        has_sse = False
        for record in records:
            sse_events = record.get("response", {}).get("sse_events")
            if sse_events and len(sse_events) > 0:
                has_sse = True
                event_types = {e.get("event") for e in sse_events if isinstance(e, dict)}
                assert "message_start" in event_types, f"Expected message_start in SSE events, got: {event_types}"
                break

        assert has_sse, "Expected at least one trace record with sse_events (streaming response)"

    @pytest.mark.timeout(180)
    def test_trace_summary(self, claude_env):
        """Verify claude-tap prints trace summary with API call count."""
        env, trace_dir, proxy_mode = claude_env

        result = _run_claude_tap(env, trace_dir, "Reply with exactly: SUMMARY_CHECK", proxy_mode=proxy_mode)
        assert result.returncode == 0

        assert "Trace summary" in result.stdout, f"Expected 'Trace summary' in stdout:\n{result.stdout[:500]}"
        assert "API calls:" in result.stdout, f"Expected 'API calls:' in stdout:\n{result.stdout[:500]}"
