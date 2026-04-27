"""Tests for trace export formats."""

from __future__ import annotations

import json

import pytest

from claude_tap.export import export_main


def _write_trace(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    record = {
        "timestamp": "2026-04-28T12:00:00",
        "turn": 1,
        "duration_ms": 123,
        "request": {
            "body": {
                "model": "claude-sonnet-4-6",
                "messages": [{"role": "user", "content": "hello from trace"}],
            }
        },
        "response": {
            "body": {
                "content": [{"type": "text", "text": "hello from assistant"}],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }
        },
    }
    trace_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    return trace_path


def test_export_html_inferred_from_output_suffix(tmp_path, capsys) -> None:
    trace_path = _write_trace(tmp_path)
    html_path = tmp_path / "trace.html"

    assert export_main([str(trace_path), "-o", str(html_path)]) == 0

    html = html_path.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in html
    assert "EMBEDDED_TRACE_DATA" in html
    assert "hello from trace" in html
    assert f"Exported 1 turns to {html_path}" in capsys.readouterr().out


def test_export_html_format_defaults_to_trace_html_path(tmp_path, capsys) -> None:
    trace_path = _write_trace(tmp_path)
    html_path = trace_path.with_suffix(".html")

    assert export_main([str(trace_path), "--format", "html"]) == 0

    assert html_path.exists()
    assert "hello from assistant" in html_path.read_text(encoding="utf-8")
    assert f"Exported 1 turns to {html_path}" in capsys.readouterr().out


def test_export_help_mentions_html(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        export_main(["--help"])

    assert exc_info.value.code == 0
    help_text = capsys.readouterr().out
    assert "{markdown,json,html}" in help_text
    assert "for HTML" in help_text
