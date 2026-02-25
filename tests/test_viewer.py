"""Tests for HTML viewer generation."""

import json
from pathlib import Path

from claude_tap.viewer import _generate_html_viewer


class TestGenerateHtmlViewer:
    """Test _generate_html_viewer() embeds JSONL data correctly."""

    def _make_trace(self, tmp_path, records: list[dict]) -> Path:
        """Write trace records to a JSONL file."""
        trace_path = tmp_path / "trace.jsonl"
        with open(trace_path, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
        return trace_path

    def test_generates_html_with_embedded_data(self, tmp_path):
        """Viewer should embed JSONL records as JavaScript array."""
        records = [
            {"turn": 1, "request": {"body": {"model": "opus"}}},
            {"turn": 2, "request": {"body": {"model": "haiku"}}},
        ]
        trace_path = self._make_trace(tmp_path, records)
        html_path = tmp_path / "trace.html"

        _generate_html_viewer(trace_path, html_path)

        assert html_path.exists()
        html = html_path.read_text()
        assert "EMBEDDED_TRACE_DATA" in html
        # Both records should be embedded
        assert '"turn":1' in html or '"turn": 1' in html
        assert '"turn":2' in html or '"turn": 2' in html

    def test_embedded_data_is_valid_javascript(self, tmp_path):
        """The embedded JS array should be syntactically valid (parseable)."""
        records = [{"turn": 1, "data": "hello"}]
        trace_path = self._make_trace(tmp_path, records)
        html_path = tmp_path / "trace.html"

        _generate_html_viewer(trace_path, html_path)
        html = html_path.read_text()

        # Extract the EMBEDDED_TRACE_DATA assignment
        marker = "const EMBEDDED_TRACE_DATA = "
        start = html.index(marker) + len(marker)
        # Find the closing bracket and semicolon
        bracket_depth = 0
        end = start
        for i in range(start, len(html)):
            if html[i] == "[":
                bracket_depth += 1
            elif html[i] == "]":
                bracket_depth -= 1
                if bracket_depth == 0:
                    end = i + 1
                    break
        json_str = html[start:end]
        data = json.loads(json_str)
        assert len(data) == 1
        assert data[0]["turn"] == 1

    def test_empty_trace_file(self, tmp_path):
        """An empty trace file should still produce valid HTML."""
        trace_path = tmp_path / "trace.jsonl"
        trace_path.write_text("")
        html_path = tmp_path / "trace.html"

        _generate_html_viewer(trace_path, html_path)

        assert html_path.exists()
        html = html_path.read_text()
        assert "EMBEDDED_TRACE_DATA" in html

    def test_nonexistent_trace_file(self, tmp_path):
        """If the trace file doesn't exist yet, viewer should still generate."""
        trace_path = tmp_path / "nonexistent.jsonl"
        html_path = tmp_path / "trace.html"

        _generate_html_viewer(trace_path, html_path)

        # Should still produce HTML (with empty data)
        assert html_path.exists()

    def test_paths_embedded_in_html(self, tmp_path):
        """The JSONL and HTML file paths should be embedded for copy-to-clipboard."""
        trace_path = self._make_trace(tmp_path, [{"turn": 1}])
        html_path = tmp_path / "trace.html"

        _generate_html_viewer(trace_path, html_path)

        html = html_path.read_text()
        assert "__TRACE_JSONL_PATH__" in html
        assert "__TRACE_HTML_PATH__" in html
        assert str(trace_path.absolute()) in html

    def test_missing_template_does_nothing(self, tmp_path, monkeypatch):
        """If viewer.html template is missing, function should return silently."""
        trace_path = self._make_trace(tmp_path, [{"turn": 1}])
        html_path = tmp_path / "trace.html"

        # Make the template lookup point to a nonexistent directory
        import claude_tap.viewer as viewer_mod

        monkeypatch.setattr(viewer_mod, "__file__", str(tmp_path / "fake" / "viewer.py"))

        _generate_html_viewer(trace_path, html_path)

        # HTML file should NOT be created since template is missing
        assert not html_path.exists()
