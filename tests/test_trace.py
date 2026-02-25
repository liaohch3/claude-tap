"""Tests for TraceWriter – JSONL writing and statistics accumulation."""

import json
from unittest.mock import AsyncMock

import pytest

from claude_tap.trace import TraceWriter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(
    model="claude-sonnet-4-20250514",
    input_tokens=100,
    output_tokens=50,
    cache_read=0,
    cache_create=0,
):
    """Build a minimal trace record with usage stats."""
    return {
        "request": {"body": {"model": model}},
        "response": {
            "body": {
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cache_read_input_tokens": cache_read,
                    "cache_creation_input_tokens": cache_create,
                }
            }
        },
    }


# ---------------------------------------------------------------------------
# JSONL writing
# ---------------------------------------------------------------------------


class TestTraceWriterIO:
    """Test that TraceWriter writes correct JSONL files."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.trace_path = tmp_path / "trace.jsonl"

    async def test_write_creates_valid_jsonl(self):
        """Each write() call should produce one JSON line in the file."""
        writer = TraceWriter(self.trace_path)
        record = _make_record()
        await writer.write(record)
        writer.close()

        lines = self.trace_path.read_text().strip().split("\n")
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["request"]["body"]["model"] == "claude-sonnet-4-20250514"

    async def test_multiple_writes_append(self):
        """Multiple writes should produce multiple lines, in order."""
        writer = TraceWriter(self.trace_path)
        for i in range(5):
            await writer.write(_make_record(model=f"model-{i}"))
        writer.close()

        lines = self.trace_path.read_text().strip().split("\n")
        assert len(lines) == 5
        for i, line in enumerate(lines):
            assert json.loads(line)["request"]["body"]["model"] == f"model-{i}"

    async def test_write_flushes_immediately(self):
        """Records should be readable from disk before close() is called."""
        writer = TraceWriter(self.trace_path)
        await writer.write(_make_record())
        # Read before close — should already be flushed
        content = self.trace_path.read_text().strip()
        assert len(content) > 0
        json.loads(content)  # Should be valid JSON
        writer.close()

    async def test_creates_parent_directory(self, tmp_path):
        """TraceWriter should create parent directories if they don't exist."""
        deep_path = tmp_path / "nested" / "dir" / "trace.jsonl"
        writer = TraceWriter(deep_path)
        await writer.write(_make_record())
        writer.close()
        assert deep_path.exists()

    async def test_close_is_idempotent(self):
        """Calling close() multiple times should not raise."""
        writer = TraceWriter(self.trace_path)
        await writer.write(_make_record())
        writer.close()
        writer.close()  # Should not raise


# ---------------------------------------------------------------------------
# Statistics accumulation
# ---------------------------------------------------------------------------


class TestTraceWriterStats:
    """Test that _update_stats correctly accumulates token usage."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.trace_path = tmp_path / "trace.jsonl"

    async def test_single_record_stats(self):
        """Stats should reflect a single record's usage."""
        writer = TraceWriter(self.trace_path)
        await writer.write(_make_record(input_tokens=100, output_tokens=50, cache_read=10, cache_create=5))
        writer.close()

        summary = writer.get_summary()
        assert summary["api_calls"] == 1
        assert summary["input_tokens"] == 100
        assert summary["output_tokens"] == 50
        assert summary["cache_read_tokens"] == 10
        assert summary["cache_create_tokens"] == 5

    async def test_multi_record_accumulation(self):
        """Stats should accumulate across multiple writes."""
        writer = TraceWriter(self.trace_path)
        await writer.write(_make_record(input_tokens=100, output_tokens=50))
        await writer.write(_make_record(input_tokens=200, output_tokens=75))
        await writer.write(_make_record(input_tokens=50, output_tokens=25))
        writer.close()

        summary = writer.get_summary()
        assert summary["api_calls"] == 3
        assert summary["input_tokens"] == 350
        assert summary["output_tokens"] == 150

    async def test_model_tracking(self):
        """models_used should count requests per model."""
        writer = TraceWriter(self.trace_path)
        await writer.write(_make_record(model="opus"))
        await writer.write(_make_record(model="opus"))
        await writer.write(_make_record(model="haiku"))
        writer.close()

        summary = writer.get_summary()
        assert summary["models_used"] == {"opus": 2, "haiku": 1}

    async def test_missing_usage_field(self):
        """Records without usage data should not crash — just contribute 0 tokens."""
        writer = TraceWriter(self.trace_path)
        record = {"request": {"body": {"model": "opus"}}, "response": {"body": {}}}
        await writer.write(record)
        writer.close()

        summary = writer.get_summary()
        assert summary["api_calls"] == 1
        assert summary["input_tokens"] == 0
        assert summary["output_tokens"] == 0

    async def test_missing_nested_keys(self):
        """Deeply missing keys should not crash."""
        writer = TraceWriter(self.trace_path)
        # Completely empty record
        await writer.write({})
        writer.close()

        summary = writer.get_summary()
        assert summary["api_calls"] == 1
        assert summary["models_used"] == {"unknown": 1}


# ---------------------------------------------------------------------------
# Live server integration
# ---------------------------------------------------------------------------


class TestTraceWriterLiveServer:
    """Test TraceWriter broadcasts to LiveViewerServer when provided."""

    async def test_broadcast_called_on_write(self, tmp_path):
        """write() should broadcast to live_server when one is set."""
        trace_path = tmp_path / "trace.jsonl"
        mock_server = AsyncMock()
        writer = TraceWriter(trace_path, live_server=mock_server)

        record = _make_record()
        await writer.write(record)
        writer.close()

        mock_server.broadcast.assert_called_once_with(record)

    async def test_no_broadcast_without_server(self, tmp_path):
        """write() should work fine without a live_server."""
        trace_path = tmp_path / "trace.jsonl"
        writer = TraceWriter(trace_path, live_server=None)
        await writer.write(_make_record())
        writer.close()
        assert writer.count == 1
