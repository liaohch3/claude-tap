"""Pytest configuration for real E2E tests.

These tests require a working `claude` CLI installation and are skipped by default.
Use --run-real-e2e to enable them.
"""

import os
import shutil
import tempfile
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
    """Verify claude-tap is importable from the current environment.

    The project should already be installed via `uv run` or `pip install -e .`.
    Returns the project root directory.
    """
    project_dir = Path(__file__).parent.parent.parent
    # Verify the package is importable (uv run handles installation)
    try:
        import claude_tap  # noqa: F401
    except ImportError:
        pytest.fail("claude_tap is not installed. Run with: uv run --extra dev pytest tests/e2e/ --run-real-e2e")
    return project_dir


@pytest.fixture
def claude_env(installed_claude_tap, monkeypatch):
    """Provide env, trace_dir, and selected proxy mode for real E2E runs.

    Mode selection:
      - CLAUDE_TAP_REAL_E2E_PROXY_MODE=reverse|forward|auto (default auto)
      - auto picks reverse when ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN is set,
        otherwise forward.
    """
    trace_dir = tempfile.mkdtemp(prefix="claude_tap_real_e2e_")
    db_path = str(Path(trace_dir) / "test-traces.sqlite3")

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["CLOUDTAP_DB"] = db_path

    # Also set in current process so the test can load records from the same database
    monkeypatch.setenv("CLOUDTAP_DB", db_path)
    from claude_tap import trace_store

    trace_store._store = None
    # Remove nesting detection vars
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_SSE_PORT", None)
    # Disable update check in tests
    env["CLAUDE_TAP_PYPI_URL"] = "http://127.0.0.1:1/invalid"

    selected_mode = env.get("CLAUDE_TAP_REAL_E2E_PROXY_MODE", "auto").lower()
    if selected_mode not in {"auto", "reverse", "forward"}:
        pytest.fail(f"CLAUDE_TAP_REAL_E2E_PROXY_MODE must be one of: auto, reverse, forward (got: {selected_mode})")

    if selected_mode == "auto":
        has_static_auth = bool(env.get("ANTHROPIC_API_KEY") or env.get("ANTHROPIC_AUTH_TOKEN"))
        selected_mode = "reverse" if has_static_auth else "forward"

    yield env, trace_dir, selected_mode

    # Keep trace dir on failure for debugging; clean on success
    shutil.rmtree(trace_dir, ignore_errors=True)
