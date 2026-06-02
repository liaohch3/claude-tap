"""Tests for Bedrock-specific dashboard helpers (usage, model, events)."""

from __future__ import annotations

import base64
import json

from claude_tap.dashboard import (
    _bedrock_events,
    _record_model,
    _record_response_text,
    _record_usage,
)


def _bedrock_frame(payload: dict) -> str:
    encoded = base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    return "\x00\x00binary-prefix" + json.dumps({"bytes": encoded, "p": "abcdefghijk"}) + "�"


def _bedrock_body(*payloads: dict) -> str:
    return "".join(_bedrock_frame(p) for p in payloads)


def _wrapped_bedrock_frame(payload: dict) -> str:
    encoded = base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    return "\x00\x00binary-prefix" + json.dumps({"chunk": {"bytes": encoded}}) + "�"


class TestBedrockEvents:
    def test_decodes_eventstream_body(self):
        body = _bedrock_body(
            {"type": "message_start", "message": {"model": "claude-opus-4-6"}},
            {"type": "message_delta", "usage": {"output_tokens": 5}},
        )
        record = {"response": {"body": body}}
        events = _bedrock_events(record)
        assert len(events) == 2
        assert events[0]["event"] == "message_start"
        assert events[1]["event"] == "message_delta"

    def test_decodes_chunk_wrapped_eventstream_body(self):
        body = _wrapped_bedrock_frame({"type": "message_start", "message": {"model": "claude-sonnet-4-6"}})
        record = {"response": {"body": body}}
        events = _bedrock_events(record)
        assert events == [
            {"event": "message_start", "data": {"type": "message_start", "message": {"model": "claude-sonnet-4-6"}}}
        ]

    def test_decodes_converse_stream_eventstream_body(self):
        body = _bedrock_body(
            {"messageStart": {"role": "assistant"}},
            {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "OK"}}},
            {"metadata": {"usage": {"inputTokens": 6, "outputTokens": 2}}},
        )
        record = {"response": {"body": body}}
        events = _bedrock_events(record)
        assert [event["event"] for event in events] == ["message_start", "content_block_delta", "message_delta"]
        assert events[1]["data"]["delta"] == {"type": "text_delta", "text": "OK"}
        assert events[2]["data"]["usage"] == {"inputTokens": 6, "outputTokens": 2}

    def test_decodes_raw_converse_stream_payloads(self):
        body = "".join(
            json.dumps(payload, separators=(",", ":"))
            for payload in (
                {"messageStart": {"role": "assistant"}},
                {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "OK"}}},
                {"metadata": {"usage": {"inputTokens": 6, "outputTokens": 2}}},
            )
        )
        record = {"response": {"body": body}}
        events = _bedrock_events(record)
        assert [event["event"] for event in events] == ["message_start", "content_block_delta", "message_delta"]

    def test_preserves_bedrock_error_events(self):
        record = {"response": {"body": json.dumps({"modelStreamErrorException": {"message": "stream failed"}})}}
        events = _bedrock_events(record)
        assert events == [{"event": "modelStreamErrorException", "data": {"message": "stream failed"}}]

    def test_returns_empty_for_non_bedrock_response(self):
        record = {"response": {"body": {"content": [], "usage": {}}}}
        assert _bedrock_events(record) == []

    def test_returns_empty_for_missing_response(self):
        assert _bedrock_events({}) == []
        assert _bedrock_events({"response": None}) == []


class TestBedrockRecordUsage:
    def test_extracts_usage_from_bedrock_events(self):
        body = _bedrock_body(
            {
                "type": "message_start",
                "message": {
                    "model": "claude-opus-4-6",
                    "usage": {"input_tokens": 10, "cache_read_input_tokens": 5, "output_tokens": 0},
                },
            },
            {"type": "message_delta", "usage": {"output_tokens": 7}},
        )
        record = {"response": {"body": body}}
        usage = _record_usage(record)
        assert usage["input_tokens"] == 10
        assert usage["output_tokens"] == 7
        assert usage["cache_read_input_tokens"] == 5

    def test_bedrock_usage_not_used_when_standard_usage_exists(self):
        record = {"response": {"body": {"usage": {"input_tokens": 3, "output_tokens": 2}}}}
        usage = _record_usage(record)
        assert usage["input_tokens"] == 3
        assert usage["output_tokens"] == 2

    def test_extracts_usage_from_bedrock_converse_response_body(self):
        record = {
            "response": {
                "body": {
                    "usage": {
                        "inputTokens": 9,
                        "outputTokens": 4,
                        "totalTokens": 13,
                        "cacheReadInputTokens": 3,
                        "cacheWriteInputTokens": 2,
                    }
                }
            }
        }
        usage = _record_usage(record)
        assert usage["input_tokens"] == 9
        assert usage["output_tokens"] == 4
        assert usage["total_tokens"] == 13
        assert usage["cache_read_input_tokens"] == 3
        assert usage["cache_creation_input_tokens"] == 2

    def test_extracts_usage_from_bedrock_converse_stream_metadata(self):
        body = _bedrock_body(
            {"messageStart": {"role": "assistant"}},
            {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "OK"}}},
            {"metadata": {"usage": {"inputTokens": 6, "outputTokens": 2, "totalTokens": 8}}},
        )
        record = {"response": {"body": body}}
        usage = _record_usage(record)
        assert usage["input_tokens"] == 6
        assert usage["output_tokens"] == 2
        assert usage["total_tokens"] == 8


def test_record_response_text_reads_bedrock_converse_output_message() -> None:
    record = {
        "response": {
            "body": {
                "output": {
                    "message": {
                        "role": "assistant",
                        "content": [{"text": "Bedrock says OK"}],
                    }
                }
            }
        }
    }
    assert _record_response_text(record) == "Bedrock says OK"


class TestBedrockRecordModel:
    def test_extracts_model_from_bedrock_message_start(self):
        body = _bedrock_body(
            {"type": "message_start", "message": {"model": "claude-opus-4-6", "usage": {}}},
        )
        record = {"response": {"body": body}}
        assert _record_model(record) == "claude-opus-4-6"

    def test_extracts_model_from_bedrock_path(self):
        record = {
            "request": {"path": "/model/us.anthropic.claude-opus-4-6-v1/invoke-with-response-stream"},
            "response": {"body": ""},
        }
        assert _record_model(record) == "us.anthropic.claude-opus-4-6-v1"

    def test_preserves_bedrock_model_version_suffix_from_path(self):
        record = {
            "request": {"path": "/model/anthropic.claude-sonnet-4-20250514-v1:0/invoke"},
            "response": {"body": ""},
        }
        assert _record_model(record) == "anthropic.claude-sonnet-4-20250514-v1:0"

    def test_extracts_model_from_bedrock_converse_paths(self):
        for suffix in ("converse", "converse-stream"):
            record = {
                "request": {"path": f"/model/anthropic.claude-sonnet-4-20250514-v1:0/{suffix}"},
                "response": {"body": ""},
            }
            assert _record_model(record) == "anthropic.claude-sonnet-4-20250514-v1:0"
