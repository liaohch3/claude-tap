from __future__ import annotations

import pytest

from claude_tap.trace import TraceWriter
from claude_tap.usage import normalize_usage


def test_normalize_usage_maps_responses_cached_tokens() -> None:
    usage = normalize_usage(
        {
            "input_tokens": 11767,
            "input_tokens_details": {"cached_tokens": 11648},
            "output_tokens": 6,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 11773,
        }
    )

    assert usage["input_tokens"] == 11767
    assert usage["output_tokens"] == 6
    assert usage["cache_read_input_tokens"] == 11648


def test_normalize_usage_maps_chat_completion_cached_tokens() -> None:
    usage = normalize_usage(
        {
            "prompt_tokens": 8,
            "completion_tokens": 5,
            "prompt_tokens_details": {"cached_tokens": 3},
            "total_tokens": 13,
        }
    )

    assert usage["input_tokens"] == 8
    assert usage["output_tokens"] == 5
    assert usage["cache_read_input_tokens"] == 3


def test_normalize_usage_preserves_explicit_anthropic_cache_read() -> None:
    usage = normalize_usage(
        {
            "input_tokens": 10,
            "output_tokens": 2,
            "cache_read_input_tokens": 4,
            "input_tokens_details": {"cached_tokens": 9},
        }
    )

    assert usage["cache_read_input_tokens"] == 4


@pytest.mark.asyncio
async def test_trace_writer_counts_responses_cached_tokens(trace_db) -> None:
    from claude_tap.trace_store import get_trace_store

    session_id = get_trace_store().create_session()
    writer = TraceWriter(session_id)
    try:
        await writer.write(
            {
                "request": {"body": {"model": "gpt-5.4"}},
                "response": {
                    "body": {
                        "usage": {
                            "input_tokens": 11767,
                            "input_tokens_details": {"cached_tokens": 11648},
                            "output_tokens": 6,
                        }
                    }
                },
            }
        )

        summary = writer.get_summary()
        assert summary["input_tokens"] == 11767
        assert summary["output_tokens"] == 6
        assert summary["cache_read_tokens"] == 11648
    finally:
        writer.close()
