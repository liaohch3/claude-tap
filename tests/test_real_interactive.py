#!/usr/bin/env python3
"""Real Interactive CLI Testing Framework.

This tests claude-tap with REAL Anthropic API calls, simulating actual user behavior
in an interactive CLI environment. Use this for integration testing and TDD during
agent development.

Usage:
    # Run a simple multi-turn test
    python tests/test_real_interactive.py

    # Run with custom prompts
    python tests/test_real_interactive.py --prompts "hello" "write hello.py" "exit"

    # Run in non-interactive mode (-p flag)
    python tests/test_real_interactive.py --print-mode -p "write a haiku"

Requirements:
    - Real claude CLI installed and authenticated
    - pexpect installed: pip install pexpect
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pytest

try:
    import pexpect
except ImportError:
    pexpect = None
    pytestmark = pytest.mark.skip(reason="pexpect not installed")


@dataclass
class InteractiveTestResult:
    """Result of an interactive test run."""

    success: bool
    turns: int
    api_calls: int
    input_tokens: int
    output_tokens: int
    trace_file: Path | None
    html_file: Path | None
    log_file: Path | None
    traces: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0


class InteractiveCLIDriver:
    """Driver for interactive CLI testing using pexpect."""

    def __init__(
        self,
        output_dir: Path | None = None,
        timeout: int = 120,
        claude_tap_args: list[str] | None = None,
    ):
        self.output_dir = output_dir or Path(tempfile.mkdtemp(prefix="claude-tap-test-"))
        self.timeout = timeout
        self.claude_tap_args = claude_tap_args or []
        self.child: pexpect.spawn | None = None
        self._log_buffer: list[str] = []

    def _log(self, msg: str):
        """Log a message."""
        self._log_buffer.append(msg)
        print(msg, flush=True)

    def start(self) -> bool:
        """Start claude-tap with the real claude CLI."""
        claude_tap_dir = Path(__file__).parent.parent

        # Ensure output directory exists
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Build command
        cmd = [
            sys.executable,
            "-m",
            "claude_tap",
            "--tap-output-dir",
            str(self.output_dir),
        ] + self.claude_tap_args

        self._log(f"🚀 Starting: {' '.join(cmd)}")
        self._log(f"📁 Output dir: {self.output_dir}")

        self.child = pexpect.spawn(
            cmd[0],
            cmd[1:],
            cwd=str(claude_tap_dir),
            timeout=self.timeout,
            encoding="utf-8",
            env=os.environ.copy(),
        )

        # Wait for proxy to start
        try:
            self.child.expect(r"listening on http", timeout=15)
            self._log("✅ Proxy started")
            return True
        except pexpect.TIMEOUT:
            self._log("❌ Timeout waiting for proxy to start")
            return False
        except pexpect.EOF:
            self._log("❌ Process exited unexpectedly")
            return False

    def wait_for_prompt(self, prompt_pattern: str = r"[>$#%] ", timeout: int | None = None) -> bool:
        """Wait for the CLI prompt."""
        timeout = timeout or self.timeout
        try:
            self.child.expect(prompt_pattern, timeout=timeout)
            return True
        except (pexpect.TIMEOUT, pexpect.EOF):
            return False

    def send_input(self, text: str, wait_for_response: bool = True, response_timeout: int = 60) -> str:
        """Send input to the CLI and optionally wait for response."""
        self._log(f"📤 Sending: {text}")
        self.child.sendline(text)

        if wait_for_response:
            # Wait for the next prompt or specific patterns
            try:
                # Look for common response indicators
                self.child.expect([r"[>$#%] ", r"\n\n", pexpect.TIMEOUT], timeout=response_timeout)
                response = self.child.before or ""
                self._log(f"📥 Response: {response[:200]}{'...' if len(response) > 200 else ''}")
                return response
            except pexpect.EOF:
                return self.child.before or ""

        return ""

    def send_and_expect(
        self,
        text: str,
        expect_pattern: str,
        timeout: int = 60,
    ) -> tuple[bool, str]:
        """Send input and wait for expected pattern."""
        self._log(f"📤 Sending: {text}")
        self.child.sendline(text)

        try:
            self.child.expect(expect_pattern, timeout=timeout)
            response = self.child.before or ""
            self._log(f"✅ Matched: {expect_pattern}")
            return True, response
        except pexpect.TIMEOUT:
            self._log(f"❌ Timeout waiting for: {expect_pattern}")
            return False, self.child.before or ""
        except pexpect.EOF:
            self._log(f"❌ EOF while waiting for: {expect_pattern}")
            return False, self.child.before or ""

    def exit_gracefully(self, exit_commands: list[str] | None = None) -> bool:
        """Try to exit the CLI gracefully."""
        exit_commands = exit_commands or ["/exit", "exit", "\x03"]  # Ctrl+C as fallback

        for cmd in exit_commands:
            self._log(f"🚪 Trying exit: {repr(cmd)}")
            if cmd == "\x03":
                self.child.sendcontrol("c")
            else:
                self.child.sendline(cmd)

            try:
                self.child.expect(pexpect.EOF, timeout=10)
                self._log("✅ Exited cleanly")
                return True
            except pexpect.TIMEOUT:
                continue

        # Force kill if nothing worked
        self._log("⚠️ Force killing process")
        self.child.terminate(force=True)
        return False

    def get_results(self) -> InteractiveTestResult:
        """Parse results from output directory."""
        result = InteractiveTestResult(
            success=False,
            turns=0,
            api_calls=0,
            input_tokens=0,
            output_tokens=0,
            trace_file=None,
            html_file=None,
            log_file=None,
        )

        # Find trace files
        jsonl_files = list(self.output_dir.glob("*.jsonl"))
        html_files = list(self.output_dir.glob("*.html"))
        log_files = list(self.output_dir.glob("*.log"))

        if jsonl_files:
            result.trace_file = jsonl_files[0]
            try:
                traces = [json.loads(line) for line in result.trace_file.read_text().splitlines() if line.strip()]
                result.traces = traces
                result.api_calls = len(traces)

                # Sum up tokens
                for trace in traces:
                    resp = trace.get("response", {}).get("body", {})
                    usage = resp.get("usage", {})
                    result.input_tokens += usage.get("input_tokens", 0)
                    result.output_tokens += usage.get("output_tokens", 0)

                result.success = True
            except Exception as e:
                result.errors.append(f"Failed to parse JSONL: {e}")

        if html_files:
            result.html_file = html_files[0]

        if log_files:
            result.log_file = log_files[0]

        return result

    def close(self):
        """Close the driver."""
        if self.child and self.child.isalive():
            self.child.terminate(force=True)


def run_interactive_test(
    prompts: list[str],
    output_dir: Path | None = None,
    timeout: int = 120,
    response_timeout: int = 60,
    assertions: list[Callable[[InteractiveTestResult], bool]] | None = None,
) -> InteractiveTestResult:
    """Run an interactive test with the given prompts.

    Args:
        prompts: List of prompts to send. Last one should trigger exit (e.g., "/exit")
        output_dir: Where to save traces (temp dir if None)
        timeout: Overall timeout
        response_timeout: Timeout for each response
        assertions: Optional list of assertion functions

    Returns:
        TestResult with all captured data
    """
    start_time = time.time()
    driver = InteractiveCLIDriver(output_dir=output_dir, timeout=timeout)

    try:
        # Start claude-tap
        if not driver.start():
            result = InteractiveTestResult(
                success=False,
                turns=0,
                api_calls=0,
                input_tokens=0,
                output_tokens=0,
                trace_file=None,
                html_file=None,
                log_file=None,
                errors=["Failed to start claude-tap"],
            )
            return result

        # Wait for Claude CLI to be ready
        time.sleep(2)

        # Send each prompt
        turns = 0
        for i, prompt in enumerate(prompts):
            is_exit = prompt.lower() in ("/exit", "exit", "quit", "/quit")

            if is_exit:
                driver.exit_gracefully([prompt])
            else:
                driver.send_input(prompt, wait_for_response=True, response_timeout=response_timeout)
                turns += 1

            # Small delay between turns
            if not is_exit:
                time.sleep(1)

        # Get results
        result = driver.get_results()
        result.turns = turns
        result.duration_seconds = time.time() - start_time

        # Run assertions
        if assertions:
            for assertion in assertions:
                try:
                    if not assertion(result):
                        result.errors.append(f"Assertion failed: {assertion.__name__}")
                        result.success = False
                except Exception as e:
                    result.errors.append(f"Assertion error: {e}")
                    result.success = False

        return result

    finally:
        driver.close()


def run_print_mode_test(
    prompt: str,
    output_dir: Path | None = None,
    timeout: int = 120,
) -> InteractiveTestResult:
    """Run a test in print mode (-p flag, non-interactive)."""
    start_time = time.time()
    output_dir = output_dir or Path(tempfile.mkdtemp(prefix="claude-tap-test-"))
    output_dir.mkdir(parents=True, exist_ok=True)

    claude_tap_dir = Path(__file__).parent.parent

    # Build command for print mode
    cmd = [
        sys.executable,
        "-m",
        "claude_tap",
        "--tap-output-dir",
        str(output_dir),
        "-p",
        prompt,  # Print mode
    ]

    print(f"🚀 Running: {' '.join(cmd)}")

    child = pexpect.spawn(
        cmd[0],
        cmd[1:],
        cwd=str(claude_tap_dir),
        timeout=timeout,
        encoding="utf-8",
        env=os.environ.copy(),
    )

    try:
        # Wait for completion
        child.expect(pexpect.EOF, timeout=timeout)
        output = child.before or ""
        print(f"📥 Output:\n{output}")
    except pexpect.TIMEOUT:
        print("❌ Timeout")
        child.terminate(force=True)

    child.close()

    # Parse results
    result = InteractiveTestResult(
        success=False,
        turns=1,
        api_calls=0,
        input_tokens=0,
        output_tokens=0,
        trace_file=None,
        html_file=None,
        log_file=None,
    )

    jsonl_files = list(output_dir.glob("*.jsonl"))
    if jsonl_files:
        result.trace_file = jsonl_files[0]
        try:
            traces = [json.loads(line) for line in result.trace_file.read_text().splitlines() if line.strip()]
            result.traces = traces
            result.api_calls = len(traces)
            result.success = True
        except Exception as e:
            result.errors.append(str(e))

    html_files = list(output_dir.glob("*.html"))
    if html_files:
        result.html_file = html_files[0]

    result.duration_seconds = time.time() - start_time
    return result


# ---------------------------------------------------------------------------
# Built-in assertions
# ---------------------------------------------------------------------------


def assert_has_traces(result: InteractiveTestResult) -> bool:
    """Assert that traces were recorded."""
    return len(result.traces) > 0


def assert_has_html(result: InteractiveTestResult) -> bool:
    """Assert that HTML viewer was generated."""
    return result.html_file is not None and result.html_file.exists()


def assert_trace_structure(result: InteractiveTestResult) -> bool:
    """Assert that traces have correct structure."""
    for trace in result.traces:
        if "turn" not in trace or "request" not in trace or "response" not in trace:
            return False
    return True


def assert_min_turns(n: int) -> Callable[[InteractiveTestResult], bool]:
    """Create assertion for minimum number of turns."""

    def check(result: InteractiveTestResult) -> bool:
        return result.api_calls >= n

    check.__name__ = f"assert_min_turns({n})"
    return check


def assert_response_contains(pattern: str) -> Callable[[InteractiveTestResult], bool]:
    """Create assertion that response contains pattern."""

    def check(result: InteractiveTestResult) -> bool:
        for trace in result.traces:
            resp_body = trace.get("response", {}).get("body", {})
            content = resp_body.get("content", [])
            for block in content:
                if block.get("type") == "text":
                    if re.search(pattern, block.get("text", ""), re.IGNORECASE):
                        return True
        return False

    check.__name__ = f"assert_response_contains({pattern!r})"
    return check


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Run real interactive CLI tests for claude-tap")
    parser.add_argument(
        "--prompts",
        "-P",
        nargs="+",
        default=["hello, say hi briefly", "/exit"],
        help="Prompts to send (last should be exit command)",
    )
    parser.add_argument("--print-mode", "-p", type=str, help="Run in print mode with single prompt")
    parser.add_argument("--output-dir", "-o", type=Path, help="Output directory for traces")
    parser.add_argument("--timeout", "-t", type=int, default=120, help="Timeout in seconds")
    parser.add_argument("--keep-traces", action="store_true", help="Keep trace files after test")

    args = parser.parse_args()

    # Determine output directory
    if args.output_dir:
        output_dir = args.output_dir
    elif args.keep_traces:
        output_dir = Path("./test-traces") / time.strftime("%Y%m%d_%H%M%S")
    else:
        output_dir = None  # Use temp dir

    print("=" * 60)
    print("🧪 Real Interactive CLI Test")
    print("=" * 60)

    if args.print_mode:
        # Non-interactive mode
        result = run_print_mode_test(
            prompt=args.print_mode,
            output_dir=output_dir,
            timeout=args.timeout,
        )
    else:
        # Interactive mode
        result = run_interactive_test(
            prompts=args.prompts,
            output_dir=output_dir,
            timeout=args.timeout,
            assertions=[
                assert_has_traces,
                assert_has_html,
                assert_trace_structure,
            ],
        )

    # Print results
    print("\n" + "=" * 60)
    print("📊 Results")
    print("=" * 60)
    print(f"Success: {'✅' if result.success else '❌'} {result.success}")
    print(f"API Calls: {result.api_calls}")
    print(f"Tokens: {result.input_tokens:,} in / {result.output_tokens:,} out")
    print(f"Duration: {result.duration_seconds:.1f}s")

    if result.trace_file:
        print(f"Trace: {result.trace_file}")
    if result.html_file:
        print(f"HTML: {result.html_file}")

    if result.errors:
        print("\n❌ Errors:")
        for error in result.errors:
            print(f"  - {error}")

    print("=" * 60)

    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
