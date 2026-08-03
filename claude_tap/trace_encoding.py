"""Shared helpers for classifying and parsing request bodies for trace storage."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

PROTOBUF_CONTENT_TYPE_TOKENS = (
    "application/proto",
    "application/x-protobuf",
    "application/connect+proto",
    "application/grpc",
)

_ENCODED_BLOB_ENCODINGS = frozenset({"protobuf", "binary"})


def content_type_from_headers(headers: Mapping[str, Any] | None) -> str:
    if not headers:
        return ""
    return str(headers.get("Content-Type") or headers.get("content-type") or "")


def is_protobuf_content_type(content_type: str) -> bool:
    lowered = content_type.lower()
    return any(token in lowered for token in PROTOBUF_CONTENT_TYPE_TOKENS)


def looks_like_binary_text(text: str) -> bool:
    if not text:
        return False
    sample = text[:256]
    if "\ufffd" in sample:
        return True
    control = sum(1 for ch in sample if ord(ch) < 32 and ch not in "\t\n\r")
    return control / max(len(sample), 1) >= 0.1


def is_encoded_blob_body(body: Any) -> bool:
    return isinstance(body, dict) and body.get("_encoding") in _ENCODED_BLOB_ENCODINGS


def parse_request_body_for_trace(body: bytes, headers: Mapping[str, str] | None = None) -> object:
    """Parse a request body for trace storage without mutating upstream bytes."""
    if not body:
        return None

    if is_protobuf_content_type(content_type_from_headers(headers)):
        return {"_encoding": "protobuf", "byte_length": len(body)}

    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return body.decode("utf-8", errors="replace")

    if isinstance(parsed, str):
        try:
            inner = json.loads(parsed)
        except (json.JSONDecodeError, ValueError):
            return parsed
        if isinstance(inner, dict):
            return inner

    return parsed
