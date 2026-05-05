"""Regression: viewer must tolerate non-dict request/response bodies.

opencode in forward-proxy mode (the new default in this PR) captures every
HTTPS upstream the client talks to — not just LLM endpoints. First-run
bootstraps proxy npm registry traffic (security advisory POSTs, .tgz
downloads, HTML error pages) whose `request.body` / `response.body` end up
as strings instead of JSON objects. The pre-fix viewer extractor assumed
dict and crashed with `AttributeError: 'str' object has no attribute 'get'`.
"""

from __future__ import annotations

import json
from pathlib import Path

from claude_tap.viewer import LAZY_THRESHOLD, _extract_metadata, _generate_html_viewer


def _record(req_body, resp_body, *, request_id: str = "req_1", turn: int = 1) -> dict:
    return {
        "timestamp": "2026-05-04T03:00:00+00:00",
        "request_id": request_id,
        "turn": turn,
        "duration_ms": 50,
        "request": {
            "method": "POST",
            "path": "/-/npm/v1/security/advisories/bulk",
            "headers": {},
            "body": req_body,
        },
        "response": {
            "status": 200,
            "headers": {},
            "body": resp_body,
        },
    }


def test_extract_metadata_handles_string_request_body() -> None:
    rec = _record("raw-string-payload-not-json", {"usage": {"input_tokens": 0}})
    meta = _extract_metadata(json.dumps(rec))
    assert meta is not None
    assert meta["request_id"] == "req_1"
    # Non-dict body should degrade gracefully — no model, no system hint.
    assert meta["model"] == ""
    assert meta["has_system"] is False
    assert meta["message_count"] == 0
    assert meta["tool_names"] == []


def test_extract_metadata_handles_string_response_body() -> None:
    rec = _record({"model": "claude-x", "messages": []}, "<!doctype html>...")
    meta = _extract_metadata(json.dumps(rec))
    assert meta is not None
    assert meta["model"] == "claude-x"
    # Non-dict response body must not block extraction of request-side fields.
    assert meta["input_tokens"] == 0
    assert meta["output_tokens"] == 0
    assert meta["response_tool_names"] == []
    assert meta["error_message"] == ""


def test_extract_metadata_handles_both_bodies_as_strings() -> None:
    rec = _record("npm-tgz-binary-blob", "gateway timeout html")
    meta = _extract_metadata(json.dumps(rec))
    assert meta is not None
    assert meta["status"] == 200
    assert meta["path"] == "/-/npm/v1/security/advisories/bulk"


def test_generate_html_viewer_does_not_crash_on_mixed_bodies(tmp_path: Path) -> None:
    """End-to-end: lazy path (>LAZY_THRESHOLD records) calls _extract_metadata
    on every line. A single string-body record must not abort generation."""
    trace_path = tmp_path / "trace.jsonl"
    lines: list[str] = []
    # Fill above the lazy threshold so _extract_metadata is exercised.
    for i in range(LAZY_THRESHOLD + 5):
        if i % 7 == 0:
            # Non-LLM npm/tgz traffic — string bodies.
            rec = _record("opaque-binary", "<html></html>", request_id=f"req_{i}", turn=i)
        else:
            rec = _record(
                {"model": "claude-x", "messages": [{"role": "user", "content": "hi"}]},
                {"usage": {"input_tokens": 1, "output_tokens": 1}, "content": []},
                request_id=f"req_{i}",
                turn=i,
            )
        lines.append(json.dumps(rec))
    trace_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    html_path = tmp_path / "trace.html"
    _generate_html_viewer(trace_path, html_path)

    assert html_path.exists()
    assert html_path.stat().st_size > 0


def _opencode_homepage_payload() -> str:
    """A minimal stand-in for the kind of HTML body the forward proxy captures
    when opencode hits a non-LLM upstream (e.g. opencode.ai homepage). The two
    pieces that matter are the </script> close-tag and a nested <script>."""
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        "<style>body{margin:0}</style>"
        "<script>window.__OC=1;</script>"
        "</head><body><h1>The open source AI coding agent</h1>"
        "<script>console.log('boot')</script></body></html>"
    )


def test_html_viewer_inline_path_escapes_script_close_in_string_body(tmp_path: Path) -> None:
    """Regression: small traces (<= LAZY_THRESHOLD) inline records as a JS
    array literal inside <script>...</script>. If a captured response.body is
    a string containing </script> (e.g. opencode.ai homepage HTML), the
    surrounding script tag would close prematurely and the HTML would render
    as page content. The inline path must escape </ -> <\\/ before embedding.
    """
    trace_path = tmp_path / "trace.jsonl"
    rec = _record(None, _opencode_homepage_payload(), request_id="req_oc_home", turn=5)
    trace_path.write_text(json.dumps(rec) + "\n", encoding="utf-8")

    html_path = tmp_path / "trace.html"
    _generate_html_viewer(trace_path, html_path)
    html = html_path.read_text(encoding="utf-8")

    # Locate the inlined data block.
    anchor = "const EMBEDDED_TRACE_DATA = ["
    start = html.find(anchor)
    assert start >= 0, "EMBEDDED_TRACE_DATA block not found in inline path"
    # Find where the JS data block is meant to end. It is followed by the
    # surrounding `</script>` that closes the data <script> block. Between
    # `start` and that close there must be NO unescaped </script>.
    data_close = html.find("</script>", start)
    assert data_close >= 0
    data_block = html[start:data_close]
    assert "</script>" not in data_block, (
        "captured </script> leaked into the inline data block — would close "
        "the wrapping <script> tag and render captured HTML as page content"
    )
    # And the escaped form must be present (proves the body did make it in).
    assert "<\\/script>" in data_block


def test_extract_metadata_recognizes_openai_function_tool_shape() -> None:
    """opencode (Chat Completions) sends tools wrapped as
    {type:"function", function:{name,description,parameters}}. The sidebar
    metadata extractor must read names from `function.name`, not just `name`."""
    rec = {
        "request_id": "req_oc",
        "turn": 1,
        "timestamp": "2026-05-04T16:00:00+00:00",
        "duration_ms": 100,
        "request": {
            "method": "POST",
            "path": "/zen/v1/chat/completions",
            "body": {
                "model": "hy3-preview-free",
                "messages": [{"role": "user", "content": "hi"}],
                "tools": [
                    {"type": "function", "function": {"name": "question", "description": "ask"}},
                    {"type": "function", "function": {"name": "bash", "description": "run"}},
                    # Defensive: a flat-Anthropic-shape tool mixed in must still resolve.
                    {"name": "edit", "description": "edit"},
                ],
            },
        },
        "response": {"status": 200, "body": {"usage": {}, "content": []}},
    }
    meta = _extract_metadata(json.dumps(rec))
    assert meta is not None
    assert meta["tool_names"] == ["question", "bash", "edit"]


def test_html_viewer_lazy_path_still_escapes_script_close(tmp_path: Path) -> None:
    """Regression: lazy path (> LAZY_THRESHOLD) embeds records in a
    <script type="text/plain" id="trace-raw"> block. Same </script> hazard."""
    trace_path = tmp_path / "trace.jsonl"
    lines: list[str] = []
    for i in range(LAZY_THRESHOLD + 5):
        if i == 3:
            rec = _record(None, _opencode_homepage_payload(), request_id=f"req_{i}", turn=i)
        else:
            rec = _record(
                {"model": "claude-x", "messages": []},
                {"usage": {}, "content": []},
                request_id=f"req_{i}",
                turn=i,
            )
        lines.append(json.dumps(rec))
    trace_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    html_path = tmp_path / "trace.html"
    _generate_html_viewer(trace_path, html_path)
    html = html_path.read_text(encoding="utf-8")

    anchor = '<script type="text/plain" id="trace-raw">'
    start = html.find(anchor)
    assert start >= 0, "trace-raw block not found in lazy path"
    block_close = html.find("</script>", start)
    assert block_close >= 0
    raw_block = html[start + len(anchor) : block_close]
    assert "</script>" not in raw_block
    assert "<\\/script>" in raw_block
