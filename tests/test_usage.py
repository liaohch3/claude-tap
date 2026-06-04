from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, cast

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


def test_normalize_usage_prefers_openai_tokens_over_zero_aliases() -> None:
    usage = normalize_usage(
        {
            "prompt_tokens": 743,
            "completion_tokens": 95,
            "total_tokens": 838,
            "prompt_tokens_details": {"cached_tokens": 0},
            "input_tokens": 0,
            "output_tokens": 0,
        }
    )

    assert usage["input_tokens"] == 743
    assert usage["output_tokens"] == 95
    assert usage["total_tokens"] == 838
    assert usage["cache_read_input_tokens"] == 0


def test_normalize_usage_maps_bedrock_converse_tokens() -> None:
    usage = normalize_usage(
        {
            "inputTokens": 12,
            "outputTokens": 3,
            "totalTokens": 15,
            "cacheReadInputTokens": 7,
            "cacheWriteInputTokens": 5,
        }
    )

    assert usage["input_tokens"] == 12
    assert usage["output_tokens"] == 3
    assert usage["total_tokens"] == 15
    assert usage["cache_read_input_tokens"] == 7
    assert usage["cache_creation_input_tokens"] == 5


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


@pytest.mark.asyncio
async def test_trace_writer_storage_errors_spool_fallback_without_interrupting_proxy_flow(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class LockedStore:
        def __init__(self) -> None:
            self.fallback_path = tmp_path / "traces.sqlite3.fallback.jsonl"

        def append_record(self, session_id: str, record: dict) -> None:
            raise sqlite3.OperationalError("database is locked")

        def finalize_session(self, session_id: str, summary: dict) -> None:
            raise sqlite3.OperationalError("database is locked")

        def append_fallback_record(self, session_id: str, record: dict, exc: sqlite3.Error) -> Path:
            return self._append_fallback("record", session_id, record, exc)

        def append_fallback_summary(self, session_id: str, summary: dict, exc: sqlite3.Error) -> Path:
            return self._append_fallback("summary", session_id, summary, exc)

        def _append_fallback(self, kind: str, session_id: str, payload: dict, exc: sqlite3.Error) -> Path:
            self.fallback_path.write_text(
                self.fallback_path.read_text(encoding="utf-8") if self.fallback_path.exists() else "",
                encoding="utf-8",
            )
            with self.fallback_path.open("a", encoding="utf-8") as file:
                file.write(json.dumps({"kind": kind, "session_id": session_id, "error": str(exc), "payload": payload}))
                file.write("\n")
            return self.fallback_path

    locked_store = LockedStore()
    writer = TraceWriter("locked-session", store=cast(Any, locked_store))

    await writer.write(
        {
            "request": {"body": {"model": "gpt-5.4"}},
            "response": {"status": 200, "body": {"usage": {"input_tokens": 3, "output_tokens": 2}}},
        }
    )
    writer.close()

    summary = writer.get_summary()
    assert summary["api_calls"] == 1
    assert summary["input_tokens"] == 3
    assert summary["output_tokens"] == 2
    assert summary["spooled_trace_records"] == 1
    assert summary["spooled_trace_summaries"] == 1
    assert summary["dropped_trace_records"] == 0
    assert summary["trace_storage_errors"] == 2
    assert capsys.readouterr().err.count("trace storage failed; spooled fallback data") == 1

    fallback_entries = [
        json.loads(line) for line in locked_store.fallback_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [entry["kind"] for entry in fallback_entries] == ["record", "summary"]
    assert fallback_entries[0]["payload"]["request"]["body"]["model"] == "gpt-5.4"
    assert fallback_entries[1]["payload"]["spooled_trace_records"] == 1
