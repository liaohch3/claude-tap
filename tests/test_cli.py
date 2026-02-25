"""Tests for CLI helper functions – argument parsing, version checking, trace cleanup."""

import json
import sys
from unittest.mock import patch

from claude_tap.cli import (
    _cleanup_traces,
    _load_manifest,
    _maybe_migrate_existing,
    _register_trace,
    _save_manifest,
    _start_background_update,
    parse_args,
)

# ---------------------------------------------------------------------------
# parse_args – additional flags not covered by test_e2e.py
# ---------------------------------------------------------------------------


class TestParseArgs:
    """Test CLI argument parsing for all --tap-* flags."""

    def test_live_viewer_flags(self):
        """--tap-live and --tap-live-port should be parsed correctly."""
        args = parse_args(["--tap-live", "--tap-live-port", "8080"])
        assert args.live_viewer is True
        assert args.live_port == 8080

    def test_live_defaults(self):
        """Live viewer should be off by default with port 0 (auto)."""
        args = parse_args([])
        assert args.live_viewer is False
        assert args.live_port == 0

    def test_max_traces_flag(self):
        """--tap-max-traces should override the default 50."""
        args = parse_args(["--tap-max-traces", "10"])
        assert args.max_traces == 10

    def test_max_traces_default(self):
        """Default max_traces should be 50."""
        args = parse_args([])
        assert args.max_traces == 50

    def test_no_update_check_flag(self):
        """--tap-no-update-check should disable update checking."""
        args = parse_args(["--tap-no-update-check"])
        assert args.no_update_check is True

    def test_no_auto_update_flag(self):
        """--tap-no-auto-update should prevent automatic downloads."""
        args = parse_args(["--tap-no-auto-update"])
        assert args.no_auto_update is True

    def test_update_flags_default_off(self):
        """Update flags should be off by default."""
        args = parse_args([])
        assert args.no_update_check is False
        assert args.no_auto_update is False

    def test_claude_args_forwarded(self):
        """Unknown flags should be forwarded to claude."""
        args = parse_args(["--tap-port", "8000", "--model", "opus", "-p", "hello"])
        assert args.port == 8000
        assert "--model" in args.claude_args
        assert "opus" in args.claude_args
        assert "-p" in args.claude_args

    def test_double_dash_separator(self):
        """-- separator should pass everything after it to claude."""
        args = parse_args(["--tap-port", "9000", "--", "--tap-live"])
        assert args.port == 9000
        # --tap-live after -- should be forwarded, not consumed by tap
        assert "--tap-live" in args.claude_args
        assert args.live_viewer is False

    def test_open_viewer_flag(self):
        """--tap-open should set open_viewer."""
        args = parse_args(["--tap-open"])
        assert args.open_viewer is True


# ---------------------------------------------------------------------------
# _start_background_update
# ---------------------------------------------------------------------------


class TestStartBackgroundUpdate:
    """Test the background update launcher."""

    def test_uv_installer_command(self):
        """uv installer should use 'uv tool upgrade'."""
        with patch("subprocess.Popen") as mock_popen:
            _start_background_update("uv")
            cmd = mock_popen.call_args[0][0]
            assert cmd == ["uv", "tool", "upgrade", "claude-tap"]

    def test_pip_installer_command(self):
        """pip installer should use 'pip install --upgrade'."""
        with patch("subprocess.Popen") as mock_popen:
            _start_background_update("pip")
            cmd = mock_popen.call_args[0][0]
            assert cmd[0] == sys.executable
            assert "--upgrade" in cmd
            assert "claude-tap" in cmd

    def test_returns_popen_on_success(self):
        """Should return the Popen object on success."""
        with patch("subprocess.Popen") as mock_popen:
            result = _start_background_update("uv")
            assert result is mock_popen.return_value

    def test_returns_none_on_failure(self):
        """Should return None if Popen raises."""
        with patch("subprocess.Popen", side_effect=OSError("no such binary")):
            result = _start_background_update("uv")
            assert result is None


# ---------------------------------------------------------------------------
# Manifest and trace cleanup
# ---------------------------------------------------------------------------


class TestManifestOperations:
    """Test manifest loading, saving, and trace registration."""

    def test_load_creates_new_manifest(self, tmp_path):
        """Loading from a directory without a manifest should create one."""
        manifest = _load_manifest(tmp_path)
        assert manifest["_cloudtap"] is True
        assert manifest["traces"] == []
        # File should have been saved
        assert (tmp_path / ".cloudtap-manifest.json").exists()

    def test_load_existing_manifest(self, tmp_path):
        """Loading an existing valid manifest should return its contents."""
        manifest_data = {"_cloudtap": True, "version": "0.1.0", "traces": [{"timestamp": "t1", "files": ["a.jsonl"]}]}
        (tmp_path / ".cloudtap-manifest.json").write_text(json.dumps(manifest_data))
        manifest = _load_manifest(tmp_path)
        assert len(manifest["traces"]) == 1

    def test_load_corrupted_manifest_resets(self, tmp_path):
        """A corrupted manifest file should be replaced with a fresh one."""
        (tmp_path / ".cloudtap-manifest.json").write_text("not valid json{{{")
        manifest = _load_manifest(tmp_path)
        assert manifest["_cloudtap"] is True
        assert manifest["traces"] == []

    def test_load_manifest_without_cloudtap_flag(self, tmp_path):
        """A JSON file missing _cloudtap flag should be treated as invalid."""
        (tmp_path / ".cloudtap-manifest.json").write_text('{"some": "data"}')
        manifest = _load_manifest(tmp_path)
        assert manifest["_cloudtap"] is True
        assert manifest["traces"] == []

    def test_register_trace_adds_entry(self, tmp_path):
        """Registering a trace should add it to the manifest."""
        manifest = _register_trace(tmp_path, "20250101_120000", ["trace.jsonl", "trace.log"])
        assert len(manifest["traces"]) == 1
        entry = manifest["traces"][0]
        assert entry["timestamp"] == "20250101_120000"
        assert "trace.jsonl" in entry["files"]
        assert "trace.log" in entry["files"]

    def test_register_multiple_traces(self, tmp_path):
        """Registering multiple traces should accumulate entries."""
        _register_trace(tmp_path, "ts1", ["a.jsonl"])
        manifest = _register_trace(tmp_path, "ts2", ["b.jsonl"])
        assert len(manifest["traces"]) == 2


class TestCleanupTraces:
    """Test automatic cleanup of old traces."""

    def _setup_traces(self, tmp_path, count):
        """Create N trace sessions with actual files."""
        # Initialize manifest first so _maybe_migrate_existing doesn't
        # pick up files created before the first _register_trace call.
        _save_manifest(tmp_path, {"_cloudtap": True, "version": "test", "traces": []})
        for i in range(count):
            ts = f"2025010{i}_120000"
            jsonl = tmp_path / f"trace_{ts}.jsonl"
            log = tmp_path / f"trace_{ts}.log"
            jsonl.write_text(f'{{"turn": {i}}}')
            log.write_text(f"log {i}")
            _register_trace(tmp_path, ts, [jsonl.name, log.name])

    def test_no_cleanup_when_under_limit(self, tmp_path):
        """No files should be deleted when count <= max_traces."""
        self._setup_traces(tmp_path, 3)
        removed = _cleanup_traces(tmp_path, max_traces=5)
        assert removed == 0

    def test_cleanup_removes_oldest(self, tmp_path):
        """Oldest traces should be removed when count exceeds max_traces."""
        self._setup_traces(tmp_path, 5)
        removed = _cleanup_traces(tmp_path, max_traces=3)
        assert removed == 2
        # Oldest files should be gone
        assert not (tmp_path / "trace_20250100_120000.jsonl").exists()
        assert not (tmp_path / "trace_20250101_120000.jsonl").exists()
        # Newest files should still exist
        assert (tmp_path / "trace_20250104_120000.jsonl").exists()

    def test_cleanup_zero_means_unlimited(self, tmp_path):
        """max_traces=0 should not delete anything."""
        self._setup_traces(tmp_path, 5)
        removed = _cleanup_traces(tmp_path, max_traces=0)
        assert removed == 0

    def test_cleanup_handles_missing_files(self, tmp_path):
        """Cleanup should not fail if files were already deleted."""
        self._setup_traces(tmp_path, 3)
        # Manually delete one file before cleanup
        for f in tmp_path.glob("trace_20250100_*"):
            f.unlink()
        removed = _cleanup_traces(tmp_path, max_traces=1)
        assert removed == 2  # Still removes entries from manifest


class TestMigratExisting:
    """Test auto-migration of pre-manifest trace files."""

    def test_discovers_existing_jsonl_files(self, tmp_path):
        """Existing trace_*.jsonl files should be added to a fresh manifest."""
        (tmp_path / "trace_20250101_000000.jsonl").write_text('{"turn": 1}')
        (tmp_path / "trace_20250101_000000.log").write_text("log")
        (tmp_path / "trace_20250101_000000.html").write_text("<html>")

        manifest = {"_cloudtap": True, "traces": []}
        _maybe_migrate_existing(tmp_path, manifest)

        assert len(manifest["traces"]) == 1
        entry = manifest["traces"][0]
        assert entry["timestamp"] == "20250101_000000"
        assert "trace_20250101_000000.jsonl" in entry["files"]
        assert "trace_20250101_000000.log" in entry["files"]
        assert "trace_20250101_000000.html" in entry["files"]

    def test_skips_already_known_files(self, tmp_path):
        """Files already in the manifest should not be re-added."""
        (tmp_path / "trace_20250101_000000.jsonl").write_text('{"turn": 1}')

        manifest = {
            "_cloudtap": True,
            "traces": [{"timestamp": "20250101_000000", "files": ["trace_20250101_000000.jsonl"]}],
        }
        _maybe_migrate_existing(tmp_path, manifest)

        # Should still have exactly 1 entry
        assert len(manifest["traces"]) == 1
