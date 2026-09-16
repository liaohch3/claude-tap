from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from claude_tap.session_briefing import TOOL_RESULT_MIN_BYTES, summarize_session, summarize_session_from_metadata
from claude_tap.summary import summary_main
from tests.test_viewer_contracts import _session_briefing_records


def test_summarize_session_reports_cost_cache_break_and_large_tool() -> None:
    briefing = summarize_session(list(_session_briefing_records()))

    assert briefing["version"] == 1
    assert briefing["cost"]["usd"] is not None
    assert briefing["cost"]["usd"] > 0
    assert "after_turn" not in briefing["cost"]
    assert "after_share" not in briefing["cost"]
    assert briefing["cache"]["break_turn"] == 2
    assert briefing["cache"]["reason"] == "cache_miss_system"
    assert briefing["cache"]["sustained"] is True
    assert briefing["tool_results"][0]["name"] == "Read"
    assert briefing["tool_results"][0]["bytes"] == 25000
    assert briefing["tool_results"][0]["turn"] == 2


def test_summarize_session_skips_image_blocks_and_small_results() -> None:
    records = [
        {
            "turn": 1,
            "request_id": "req_small",
            "request": {
                "method": "POST",
                "path": "/v1/messages",
                "headers": {},
                "body": {
                    "model": "claude-opus-5",
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "content": [
                                        {"type": "image", "source": {"type": "base64", "data": "x" * 50000}},
                                        {"type": "text", "text": "tiny"},
                                    ],
                                }
                            ],
                        }
                    ],
                },
            },
            "response": {
                "status": 200,
                "body": {"usage": {"input_tokens": 10, "output_tokens": 4}},
            },
        }
    ]

    briefing = summarize_session(records)
    assert briefing["tool_results"] == []
    assert all(item["bytes"] >= TOOL_RESULT_MIN_BYTES for item in briefing["tool_results"])


def test_summarize_session_from_metadata_keeps_cost_without_tool_bodies() -> None:
    full = summarize_session(list(_session_briefing_records()))
    from claude_tap.viewer import _extract_metadata_from_record

    metadata = [_extract_metadata_from_record(record) for record in _session_briefing_records()]
    briefing = summarize_session_from_metadata([item for item in metadata if item is not None])

    assert briefing["cost"]["usd"] == full["cost"]["usd"]
    assert briefing["cache"]["break_turn"] == 2
    assert briefing["cache"]["reason"] is None
    assert briefing["cache"]["sustained"] is True
    assert briefing["tool_results"] == []


def _cache_row(turn: int, *, read: int, write: int) -> dict:
    """A minimal record carrying only the cache token buckets the briefing reads."""
    return {
        "turn": turn,
        "request_id": f"req_cache_{turn}",
        "request": {
            "method": "POST",
            "path": "/v1/messages",
            "headers": {},
            "body": {"model": "claude-opus-5", "system": "stable", "messages": []},
        },
        "response": {
            "status": 200,
            "body": {
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 5,
                    "cache_read_input_tokens": read,
                    "cache_creation_input_tokens": write,
                }
            },
        },
    }


def test_a_cold_write_that_resumes_hitting_is_not_a_sustained_break() -> None:
    """A rebuild mid-session must not claim the cache missed from there onward."""
    records = [
        _cache_row(1, read=0, write=35000),
        _cache_row(2, read=34000, write=700),
        _cache_row(3, read=0, write=35000),
        _cache_row(4, read=35000, write=90),
        _cache_row(5, read=35000, write=90),
    ]

    cache = summarize_session(records)["cache"]
    assert cache["break_turn"] == 3
    assert cache["sustained"] is False


def test_a_cold_write_with_no_later_hit_stays_sustained() -> None:
    records = [
        _cache_row(1, read=0, write=35000),
        _cache_row(2, read=34000, write=700),
        _cache_row(3, read=0, write=35000),
        _cache_row(4, read=0, write=35000),
    ]

    cache = summarize_session(records)["cache"]
    assert cache["break_turn"] == 3
    assert cache["sustained"] is True


def test_one_tool_result_repeated_across_turns_is_reported_once() -> None:
    """The same payload rides along in every later request body; collapse it."""
    large = "y" * 30000

    def turn_with_result(turn: int) -> dict:
        return {
            "turn": turn,
            "request_id": f"req_repeat_{turn}",
            "request": {
                "method": "POST",
                "path": "/v1/messages",
                "headers": {},
                "body": {
                    "model": "claude-opus-5",
                    "messages": [
                        {
                            "role": "assistant",
                            "content": [{"type": "tool_use", "id": "toolu_a", "name": "Bash", "input": {}}],
                        },
                        {
                            "role": "user",
                            "content": [{"type": "tool_result", "tool_use_id": "toolu_a", "content": large}],
                        },
                    ],
                },
            },
            "response": {"status": 200, "body": {"usage": {"input_tokens": 50, "output_tokens": 5}}},
        }

    briefing = summarize_session([turn_with_result(turn) for turn in (7, 8, 9)])

    assert len(briefing["tool_results"]) == 1
    assert briefing["tool_results"][0]["name"] == "Bash"
    assert briefing["tool_results"][0]["bytes"] == 30000
    assert briefing["tool_results"][0]["turn"] == 7


def _embedded_briefing(html: str) -> dict:
    match = re.search(r"const EMBEDDED_SESSION_BRIEFING = (.*?);\n", html, re.S)
    assert match is not None, "viewer HTML carries no briefing constant"
    return json.loads(match.group(1))


def test_online_viewer_uses_full_records_for_the_briefing_when_given_them(tmp_path: Path) -> None:
    """The dashboard session page holds full records, so its banner must not degrade."""
    from claude_tap.viewer import _extract_metadata_from_record, _generate_html_viewer_from_metadata

    records = list(_session_briefing_records())
    metadata = [item for record in records if (item := _extract_metadata_from_record(record)) is not None]

    def render(**kwargs: object) -> dict:
        html_path = tmp_path / f"viewer-{len(kwargs)}.html"
        _generate_html_viewer_from_metadata(
            metadata,
            html_path,
            display_trace_path="compact",
            display_html_path="page",
            records_api_path="records",
            **kwargs,  # type: ignore[arg-type]
        )
        return _embedded_briefing(html_path.read_text(encoding="utf-8"))

    stubs = render()
    full = render(briefing_records=records)

    # Stub fallback stays available for callers without bodies.
    assert stubs["cache"]["reason"] is None
    assert stubs["tool_results"] == []
    # With records in hand, the page matches an exported HTML.
    assert full["cache"]["reason"] == "cache_miss_system"
    assert full["tool_results"][0]["name"] == "Read"
    assert full["cost"] == stubs["cost"]


def test_summary_cli_prints_the_same_object(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    trace = tmp_path / "briefing.jsonl"
    trace.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in _session_briefing_records()),
        encoding="utf-8",
    )

    assert summary_main([str(trace)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed == summarize_session(list(_session_briefing_records()))


def test_summary_cli_rejects_a_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing = tmp_path / "nope.jsonl"
    assert summary_main([str(missing)]) == 1
    assert "not found" in capsys.readouterr().err
