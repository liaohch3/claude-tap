"""Bedrock path helpers shared by proxy and dashboard code."""

from __future__ import annotations

import re
from urllib.parse import unquote

BEDROCK_STREAM_SUFFIXES = ("/invoke-with-response-stream", "/converse-stream")

_BEDROCK_MODEL_PATH_RE = re.compile(
    r"/model/(.+)/(?:invoke|invoke-with-response-stream|messages|converse|converse-stream)(?:[?#].*)?$"
)


def is_bedrock_eventstream_path(path: str) -> bool:
    """Return True for Bedrock routes that return AWS EventStream responses."""
    clean_path = path.split("?", 1)[0].rstrip("/")
    return clean_path.endswith(BEDROCK_STREAM_SUFFIXES)


def bedrock_model_from_path(path: str) -> str:
    """Extract the Bedrock model ID from a /model/{modelId}/... route."""
    match = _BEDROCK_MODEL_PATH_RE.search(path)
    return unquote(match.group(1)) if match else ""
