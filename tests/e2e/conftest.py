"""Pytest configuration for real E2E tests.

These tests require a working `claude` CLI installation and are skipped by default.
Use --run-real-e2e to enable them.
"""

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest


def pytest_addoption(parser):
    """Add --run-real-e2e command-line flag."""
    parser.addoption(
        "--run-real-e2e",
        action="store_true",
        default=False,
        help="Run real E2E tests that require a working claude CLI.",
    )


def pytest_collection_modifyitems(config, items):
    """Skip real E2E tests unless --run-real-e2e is passed."""
    if config.getoption("--run-real-e2e"):
        return
    skip_marker = pytest.mark.skip(reason="Need --run-real-e2e flag to run real E2E tests")
    e2e_dir = str(Path(__file__).parent)
    for item in items:
        if str(item.fspath).startswith(e2e_dir):
            item.add_marker(skip_marker)


@pytest.fixture(scope="session")
def installed_claude_tap():
    """Ensure claude-tap is installed from local source in editable mode.

    Returns the project root directory.
    """
    project_dir = Path(__file__).parent.parent.parent
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-e", str(project_dir)],
        check=True,
        capture_output=True,
        text=True,
    )
    return project_dir


@pytest.fixture
def proxy_server(installed_claude_tap):
    """Start claude-tap in proxy-only mode (--tap-no-launch).

    Reads the port from stdout, yields (port, trace_dir, proc), and
    cleans up with SIGINT on teardown.
    """
    trace_dir = tempfile.mkdtemp(prefix="claude_tap_real_e2e_")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "claude_tap",
            "--tap-no-launch",
            "--tap-port",
            "0",
            "--tap-output-dir",
            trace_dir,
            "--tap-no-update-check",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )

    # Wait for the proxy to print the listening port
    port = None
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                stderr = proc.stderr.read()
                raise RuntimeError(f"claude-tap exited early (code {proc.returncode}): {stderr}")
            time.sleep(0.1)
            continue
        # Look for the listening line: "claude-tap v... listening on http://host:port"
        if "listening on" in line:
            # Extract port from URL like http://127.0.0.1:12345
            url_part = line.strip().split("listening on")[-1].strip()
            port = int(url_part.rsplit(":", 1)[-1])
            break

    if port is None:
        proc.kill()
        stderr = proc.stderr.read()
        raise RuntimeError(f"Timed out waiting for claude-tap to start. stderr: {stderr}")

    yield port, trace_dir, proc

    # Cleanup: send SIGINT for graceful shutdown
    if proc.poll() is None:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    shutil.rmtree(trace_dir, ignore_errors=True)


@pytest.fixture
def claude_env(proxy_server):
    """Build an environment dict for running claude CLI through the proxy.

    Sets ANTHROPIC_BASE_URL to the proxy and removes env vars that
    would interfere with Claude Code.
    """
    port, trace_dir, proc = proxy_server
    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{port}"
    env["NO_PROXY"] = "127.0.0.1"
    # Remove Claude Code nesting detection vars
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_SSE_PORT", None)
    return env, trace_dir
