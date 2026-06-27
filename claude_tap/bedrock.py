"""Bedrock path helpers shared by proxy and dashboard code."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote

BEDROCK_STREAM_SUFFIXES = ("/invoke-with-response-stream", "/converse-stream")
BEDROCK_ERROR_EVENT_KEYS = frozenset(
    {
        "internalServerException",
        "modelStreamErrorException",
        "modelTimeoutException",
        "serviceUnavailableException",
        "throttlingException",
        "validationException",
    }
)

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


def bedrock_error_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return normalized Bedrock EventStream error events."""
    errors: list[dict[str, Any]] = []
    for event in events:
        event_type = event.get("event")
        if not isinstance(event_type, str) or event_type not in BEDROCK_ERROR_EVENT_KEYS:
            continue
        data = event.get("data")
        error = {"type": event_type}
        if isinstance(data, dict):
            error.update(data)
        elif data is not None:
            error["message"] = str(data)
        errors.append(error)
    return errors


def attach_bedrock_errors(body: object, events: list[dict[str, Any]]) -> object:
    """Persist Bedrock stream errors even when raw stream events are omitted."""
    errors = bedrock_error_events(events)
    if not errors:
        return body

    first_error = errors[0]
    if isinstance(body, dict):
        annotated = dict(body)
    else:
        annotated = {"raw_body": body}
    annotated.setdefault("error", first_error)
    annotated["bedrock_errors"] = errors
    return annotated
