#!/usr/bin/env python3
"""Real API Testing Framework for claude-tap.

Tests claude-tap with REAL Anthropic API calls. Supports both:
1. Print mode (-p): Non-interactive, single prompt
2. Interactive mode: Multi-turn via stdin pipe

Usage:
    # Simple print mode test
    python tests/test_real_api.py -p "say hello"

    # Multi-turn via stdin pipe
    python tests/test_real_api.py --stdin "hello" "write fib" "/exit"

    # Keep traces for inspection
    python tests/test_real_api.py -p "hello" --keep

    # Run as pytest
    pytest tests/test_real_api.py -v
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest


@dataclass
class TraceResult:
    """Result of a claude-tap test run."""

    success: bool = False
    api_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    traces: list[dict] = field(default_factory=list)
    trace_file: Path | None = None
    html_file: Path | None = None
    log_file: Path | None = None
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    duration_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)


def run_claude_tap(
    args: list[str],
    stdin_input: str | None = None,
    output_dir: Path | None = None,
    timeout: int = 120,
    cwd: Path | None = None,
) -> TraceResult:
    """Run claude-tap with given arguments and return results.

    Args:
        args: Arguments to pass to claude-tap (after the command)
        stdin_input: Optional stdin to pipe in
        output_dir: Directory for trace output
        timeout: Timeout in seconds
        cwd: Working directory

    Returns:
        TraceResult with all captured data
    """
    result = TraceResult()
    start_time = time.time()

    # Setup output directory
    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="claude-tap-test-"))
    output_dir.mkdir(parents=True, exist_ok=True)

    # Working directory
    work_dir = cwd or Path.cwd()

    # Build command - use installed claude-tap directly
    cmd = [
        "claude-tap",
        "--tap-output-dir",
        str(output_dir),
    ] + args

    try:
        # If no stdin_input, use /dev/null to avoid blocking
        stdin_arg = stdin_input if stdin_input else ""
        proc = subprocess.run(
            cmd,
            input=stdin_arg,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(work_dir),
            env=os.environ.copy(),
        )
        result.stdout = proc.stdout
        result.stderr = proc.stderr
        result.exit_code = proc.returncode
    except subprocess.TimeoutExpired as e:
        result.errors.append(f"Timeout after {timeout}s")
        result.stdout = e.stdout.decode() if e.stdout else ""
        result.stderr = e.stderr.decode() if e.stderr else ""
    except Exception as e:
        result.errors.append(str(e))

    result.duration_seconds = time.time() - start_time

    # Parse trace files
    jsonl_files = list(output_dir.glob("*.jsonl"))
    html_files = list(output_dir.glob("*.html"))
    log_files = list(output_dir.glob("*.log"))

    if jsonl_files:
        result.trace_file = jsonl_files[0]
        try:
            traces = [json.loads(line) for line in result.trace_file.read_text().splitlines() if line.strip()]
            result.traces = traces
            result.api_calls = len(traces)

            # Sum up tokens
            for trace in traces:
                resp_body = trace.get("response", {}).get("body", {})
                usage = resp_body.get("usage", {})
                result.input_tokens += usage.get("input_tokens", 0)
                result.output_tokens += usage.get("output_tokens", 0)
                result.cache_read_tokens += usage.get("cache_read_input_tokens", 0)
                result.cache_write_tokens += usage.get("cache_creation_input_tokens", 0)

            result.success = True
        except Exception as e:
            result.errors.append(f"Failed to parse JSONL: {e}")

    if html_files:
        result.html_file = html_files[0]

    if log_files:
        result.log_file = log_files[0]

    return result


def run_print_mode(
    prompt: str,
    output_dir: Path | None = None,
    timeout: int = 120,
    extra_args: list[str] | None = None,
) -> TraceResult:
    """Run claude-tap in print mode (-p) with a single prompt.

    Note: claude-tap uses parse_known_args, so -p and prompt are automatically
    forwarded to claude without needing --.
    """
    args = ["-p", prompt] + (extra_args or [])
    # Provide empty stdin to avoid blocking
    return run_claude_tap(args, stdin_input="", output_dir=output_dir, timeout=timeout)


def run_stdin_mode(
    prompts: list[str],
    output_dir: Path | None = None,
    timeout: int = 120,
    extra_args: list[str] | None = None,
) -> TraceResult:
    """Run claude-tap with prompts piped via stdin."""
    # Join prompts with newlines
    stdin_input = "\n".join(prompts) + "\n"
    args = extra_args or []
    return run_claude_tap(args, stdin_input=stdin_input, output_dir=output_dir, timeout=timeout)


# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------


def assert_has_traces(result: TraceResult) -> None:
    """Assert that traces were recorded."""
    assert len(result.traces) > 0, "No traces recorded"


def assert_min_api_calls(result: TraceResult, n: int) -> None:
    """Assert minimum number of API calls."""
    assert result.api_calls >= n, f"Expected at least {n} API calls, got {result.api_calls}"


def assert_has_html_viewer(result: TraceResult) -> None:
    """Assert HTML viewer was generated."""
    assert result.html_file is not None, "No HTML file generated"
    assert result.html_file.exists(), f"HTML file does not exist: {result.html_file}"


def assert_trace_structure(result: TraceResult) -> None:
    """Assert all traces have correct structure."""
    for i, trace in enumerate(result.traces):
        assert "turn" in trace, f"Trace {i} missing 'turn'"
        assert "request" in trace, f"Trace {i} missing 'request'"
        assert "response" in trace, f"Trace {i} missing 'response'"


def assert_response_contains(result: TraceResult, pattern: str) -> None:
    """Assert at least one response contains the pattern."""
    import re

    for trace in result.traces:
        content = trace.get("response", {}).get("body", {}).get("content", [])
        for block in content:
            if block.get("type") == "text":
                if re.search(pattern, block.get("text", ""), re.IGNORECASE):
                    return
    raise AssertionError(f"No response contains pattern: {pattern}")


# ---------------------------------------------------------------------------
# pytest fixtures and tests
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_output_dir():
    """Create a temporary output directory."""
    with tempfile.TemporaryDirectory(prefix="claude-tap-test-") as tmp:
        yield Path(tmp)


class TestPrintMode:
    """Tests for print mode (-p)."""

    @pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not installed")
    def test_simple_prompt(self, temp_output_dir):
        """Test a simple prompt in print mode."""
        result = run_print_mode(
            "respond with only the word 'hello'",
            output_dir=temp_output_dir,
            timeout=60,
        )

        # Basic assertions
        assert_has_traces(result)
        assert_has_html_viewer(result)
        assert_trace_structure(result)

        print("\n✅ Test passed!")
        print(f"   API calls: {result.api_calls}")
        print(f"   Tokens: {result.input_tokens:,} in / {result.output_tokens:,} out")
        print(f"   Duration: {result.duration_seconds:.1f}s")


class TestIntegration:
    """Integration tests with real API."""

    @pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not installed")
    def test_trace_recording(self, temp_output_dir):
        """Test that traces are properly recorded."""
        result = run_print_mode(
            "say hello",
            output_dir=temp_output_dir,
        )

        # Verify trace file
        assert result.trace_file is not None
        assert result.trace_file.exists()

        # Verify trace content
        traces = result.traces
        assert len(traces) >= 1

        # Each trace should have required fields
        for trace in traces:
            assert "timestamp" in trace
            assert "request" in trace
            assert "response" in trace
            assert trace["request"].get("method") == "POST"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Run real API tests for claude-tap")
    parser.add_argument("-p", "--print", dest="print_prompt", help="Run in print mode with prompt")
    parser.add_argument("--stdin", nargs="+", help="Run with prompts via stdin")
    parser.add_argument("-o", "--output", type=Path, help="Output directory")
    parser.add_argument("-t", "--timeout", type=int, default=120, help="Timeout in seconds")
    parser.add_argument("--keep", action="store_true", help="Keep output directory")

    args = parser.parse_args()

    # Determine output directory
    if args.output:
        output_dir = args.output
    elif args.keep:
        output_dir = Path("./test-traces") / time.strftime("%Y%m%d_%H%M%S")
    else:
        output_dir = None

    print("=" * 60)
    print("🧪 Real API Test for claude-tap")
    print("=" * 60)

    if args.print_prompt:
        print(f"\n📤 Prompt: {args.print_prompt}")
        result = run_print_mode(args.print_prompt, output_dir=output_dir, timeout=args.timeout)
    elif args.stdin:
        print(f"\n📤 Prompts: {args.stdin}")
        result = run_stdin_mode(args.stdin, output_dir=output_dir, timeout=args.timeout)
    else:
        # Default test
        print("\n📤 Running default test...")
        result = run_print_mode("say hello briefly", output_dir=output_dir, timeout=args.timeout)

    # Print results
    print("\n" + "=" * 60)
    print("📊 Results")
    print("=" * 60)
    print(f"Success: {'✅' if result.success else '❌'}")
    print(f"Exit code: {result.exit_code}")
    print(f"API calls: {result.api_calls}")
    print(f"Tokens: {result.input_tokens:,} in / {result.output_tokens:,} out")
    if result.cache_read_tokens or result.cache_write_tokens:
        print(f"Cache: {result.cache_read_tokens:,} read / {result.cache_write_tokens:,} write")
    print(f"Duration: {result.duration_seconds:.1f}s")

    if result.trace_file:
        print(f"\n📁 Trace: {result.trace_file}")
    if result.html_file:
        print(f"📁 HTML: {result.html_file}")

    if result.errors:
        print("\n❌ Errors:")
        for err in result.errors:
            print(f"  - {err}")

    if result.stdout:
        print("\n📤 Stdout:")
        print(result.stdout[:1000])

    print("=" * 60)
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
