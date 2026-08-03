from __future__ import annotations

import json

from claude_tap.proxy import _parse_request_body_for_trace


def test_parse_request_body_for_trace_unwraps_double_serialized_object() -> None:
    inner = {"model": "claude-test", "messages": [{"role": "user", "content": "hi"}]}
    body = json.dumps(json.dumps(inner)).encode()

    assert _parse_request_body_for_trace(body) == inner


def test_parse_request_body_for_trace_keeps_double_serialized_non_object() -> None:
    inner = ["not", "a", "request", "object"]
    body = json.dumps(json.dumps(inner)).encode()

    assert _parse_request_body_for_trace(body) == json.dumps(inner)


def test_parse_request_body_for_trace_keeps_plain_json_string() -> None:
    body = json.dumps("plain prompt").encode()

    assert _parse_request_body_for_trace(body) == "plain prompt"


def test_parse_request_body_for_trace_decodes_invalid_json_as_text() -> None:
    assert _parse_request_body_for_trace(b"{not-json") == "{not-json"


def test_parse_request_body_for_trace_empty_body_is_none() -> None:
    assert _parse_request_body_for_trace(b"") is None


def test_parse_request_body_for_trace_protobuf_content_type_is_placeholder() -> None:
    body = b"\x12\x04prod\x1a\x08moredata"

    assert _parse_request_body_for_trace(
        body,
        {"Content-Type": "application/proto"},
    ) == {"_encoding": "protobuf", "byte_length": len(body)}

    assert _parse_request_body_for_trace(
        body,
        {"content-type": "application/connect+proto"},
    ) == {"_encoding": "protobuf", "byte_length": len(body)}
