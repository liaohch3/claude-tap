"""Tests for proxy module – header filtering, record building, and decompression."""

from datetime import datetime

from claude_tap.proxy import HOP_BY_HOP, _build_record, filter_headers

# ---------------------------------------------------------------------------
# filter_headers
# ---------------------------------------------------------------------------


class TestFilterHeaders:
    """Test header filtering and redaction logic."""

    def test_removes_hop_by_hop_headers(self):
        """All hop-by-hop headers should be stripped."""
        headers = {h: "value" for h in HOP_BY_HOP}
        headers["Content-Type"] = "application/json"
        result = filter_headers(headers)
        assert result == {"Content-Type": "application/json"}

    def test_redacts_api_key(self):
        """x-api-key should be truncated when redact_keys=True."""
        headers = {"x-api-key": "sk-ant-api03-very-long-secret-key-here"}
        result = filter_headers(headers, redact_keys=True)
        assert result["x-api-key"] == "sk-ant-api03..."
        assert "secret" not in result["x-api-key"]

    def test_redacts_authorization(self):
        """Authorization header should be truncated when redact_keys=True."""
        headers = {"Authorization": "Bearer sk-ant-long-token-value"}
        result = filter_headers(headers, redact_keys=True)
        assert result["Authorization"].endswith("...")
        assert "long-token" not in result["Authorization"]

    def test_short_key_fully_redacted(self):
        """Short API keys (<=12 chars) should be fully redacted."""
        headers = {"x-api-key": "short"}
        result = filter_headers(headers, redact_keys=True)
        assert result["x-api-key"] == "***"

    def test_no_redaction_by_default(self):
        """Without redact_keys, sensitive headers pass through unchanged."""
        headers = {"x-api-key": "sk-ant-api03-secret"}
        result = filter_headers(headers)
        assert result["x-api-key"] == "sk-ant-api03-secret"

    def test_preserves_normal_headers(self):
        """Non-hop-by-hop, non-sensitive headers should pass through unchanged."""
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
            "X-Custom": "my-value",
        }
        result = filter_headers(headers, redact_keys=True)
        assert result == headers

    def test_case_insensitive_hop_by_hop(self):
        """Hop-by-hop filtering should be case-insensitive."""
        headers = {"Transfer-Encoding": "chunked", "Content-Type": "text/plain"}
        result = filter_headers(headers)
        assert "Transfer-Encoding" not in result
        assert result["Content-Type"] == "text/plain"


# ---------------------------------------------------------------------------
# _build_record
# ---------------------------------------------------------------------------


class TestBuildRecord:
    """Test trace record construction."""

    def _call(self, **kwargs):
        """Call _build_record with sensible defaults, overriding with kwargs."""
        defaults = {
            "req_id": "req_abc123",
            "turn": 1,
            "duration_ms": 500,
            "method": "POST",
            "path_qs": "/v1/messages",
            "req_headers": {"Content-Type": "application/json", "x-api-key": "sk-ant-api03-secret-key"},
            "req_body": {"model": "claude-sonnet-4-20250514", "messages": [{"role": "user", "content": "hi"}]},
            "status": 200,
            "resp_headers": {"Content-Type": "application/json"},
            "resp_body": {"content": [{"type": "text", "text": "hello"}]},
        }
        defaults.update(kwargs)
        return _build_record(**defaults)

    def test_basic_structure(self):
        """Record should have all expected top-level keys."""
        record = self._call()
        assert record["request_id"] == "req_abc123"
        assert record["turn"] == 1
        assert record["duration_ms"] == 500
        assert "timestamp" in record
        assert record["request"]["method"] == "POST"
        assert record["request"]["path"] == "/v1/messages"
        assert record["response"]["status"] == 200

    def test_request_headers_redacted(self):
        """API key in request headers should be redacted in the record."""
        record = self._call()
        assert "secret" not in record["request"]["headers"].get("x-api-key", "")

    def test_response_headers_not_redacted(self):
        """Response headers should not be redacted."""
        record = self._call(resp_headers={"x-request-id": "req-12345"})
        assert record["response"]["headers"]["x-request-id"] == "req-12345"

    def test_sse_events_included_when_provided(self):
        """SSE events should be included in the record when passed."""
        events = [{"event": "message_start", "data": {}}]
        record = self._call(sse_events=events)
        assert record["response"]["sse_events"] == events

    def test_sse_events_absent_when_none(self):
        """sse_events key should not appear when not provided."""
        record = self._call()
        assert "sse_events" not in record["response"]

    def test_null_request_body(self):
        """Record should handle None request body (e.g. GET requests)."""
        record = self._call(req_body=None)
        assert record["request"]["body"] is None

    def test_null_response_body(self):
        """Record should handle None response body."""
        record = self._call(resp_body=None)
        assert record["response"]["body"] is None

    def test_timestamp_is_iso_format(self):
        """Timestamp should be a valid ISO 8601 string."""
        record = self._call()
        # Should not raise
        datetime.fromisoformat(record["timestamp"])
