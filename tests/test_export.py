"""Tests for the export module – converting trace JSONL to Markdown/JSON."""

import json
from pathlib import Path

from claude_tap.export import _export_json, _export_markdown, export_main

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(
    turn=1,
    model="claude-sonnet-4-20250514",
    user_content="hello",
    assistant_text="Hi there!",
    input_tokens=100,
    output_tokens=50,
    cache_read=0,
    cache_create=0,
    duration_ms=500,
    tool_use=None,
    thinking=None,
    tool_result=None,
):
    """Build a realistic trace record for testing."""
    # Build user message
    if tool_result is not None:
        user_msg = {"role": "user", "content": tool_result}
    else:
        user_msg = {"role": "user", "content": user_content}

    # Build response content
    content = []
    if thinking:
        content.append({"type": "thinking", "thinking": thinking})
    if assistant_text:
        content.append({"type": "text", "text": assistant_text})
    if tool_use:
        content.append({"type": "tool_use", "name": tool_use["name"], "input": tool_use["input"]})

    return {
        "turn": turn,
        "timestamp": "2025-01-01T00:00:00Z",
        "duration_ms": duration_ms,
        "request": {
            "method": "POST",
            "path": "/v1/messages",
            "body": {
                "model": model,
                "messages": [user_msg],
            },
        },
        "response": {
            "status": 200,
            "body": {
                "content": content,
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cache_read_input_tokens": cache_read,
                    "cache_creation_input_tokens": cache_create,
                },
                "stop_reason": "end_turn",
            },
        },
    }


def _write_jsonl(path: Path, records: list[dict]):
    """Write records as JSONL to path."""
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


# ---------------------------------------------------------------------------
# export_main CLI tests
# ---------------------------------------------------------------------------


class TestExportMainCLI:
    """Test the export_main() entry point for correct CLI behavior."""

    def test_missing_file_returns_error(self, tmp_path):
        """Exporting a nonexistent file should print an error and return 1."""
        result = export_main([str(tmp_path / "nonexistent.jsonl")])
        assert result == 1

    def test_empty_file_returns_error(self, tmp_path):
        """An empty trace file has no records — should return 1."""
        trace = tmp_path / "empty.jsonl"
        trace.write_text("")
        result = export_main([str(trace)])
        assert result == 1

    def test_file_with_only_invalid_json(self, tmp_path):
        """A file with only invalid JSON lines should return 1 (no valid records)."""
        trace = tmp_path / "bad.jsonl"
        trace.write_text("not json\nalso not json\n")
        result = export_main([str(trace)])
        assert result == 1

    def test_markdown_to_stdout(self, tmp_path, capsys):
        """Default export prints markdown to stdout."""
        trace = tmp_path / "trace.jsonl"
        _write_jsonl(trace, [_make_record()])
        result = export_main([str(trace)])
        assert result == 0
        out = capsys.readouterr().out
        assert "# Claude Trace Export" in out
        assert "Hi there!" in out

    def test_json_format_flag(self, tmp_path, capsys):
        """--format json outputs valid JSON."""
        trace = tmp_path / "trace.jsonl"
        _write_jsonl(trace, [_make_record()])
        result = export_main([str(trace), "--format", "json"])
        assert result == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["model"] == "claude-sonnet-4-20250514"

    def test_output_file(self, tmp_path):
        """Writing to an output file creates the file with correct content."""
        trace = tmp_path / "trace.jsonl"
        out_file = tmp_path / "export.md"
        _write_jsonl(trace, [_make_record()])
        result = export_main([str(trace), "-o", str(out_file)])
        assert result == 0
        assert out_file.exists()
        content = out_file.read_text()
        assert "# Claude Trace Export" in content

    def test_format_inferred_from_json_extension(self, tmp_path):
        """When output path ends in .json, format should auto-select JSON."""
        trace = tmp_path / "trace.jsonl"
        out_file = tmp_path / "export.json"
        _write_jsonl(trace, [_make_record()])
        result = export_main([str(trace), "-o", str(out_file)])
        assert result == 0
        data = json.loads(out_file.read_text())
        assert isinstance(data, list)

    def test_format_inferred_from_md_extension(self, tmp_path):
        """When output path ends in .md (not .json), should use markdown."""
        trace = tmp_path / "trace.jsonl"
        out_file = tmp_path / "export.md"
        _write_jsonl(trace, [_make_record()])
        result = export_main([str(trace), "-o", str(out_file)])
        assert result == 0
        content = out_file.read_text()
        assert "# Claude Trace Export" in content

    def test_records_sorted_by_turn(self, tmp_path, capsys):
        """Records should appear sorted by turn number regardless of file order."""
        trace = tmp_path / "trace.jsonl"
        _write_jsonl(
            trace,
            [
                _make_record(turn=3, assistant_text="third"),
                _make_record(turn=1, assistant_text="first"),
                _make_record(turn=2, assistant_text="second"),
            ],
        )
        result = export_main([str(trace)])
        assert result == 0
        out = capsys.readouterr().out
        # "first" should appear before "third" in the output
        assert out.index("first") < out.index("second") < out.index("third")

    def test_skips_invalid_json_lines(self, tmp_path, capsys):
        """Valid records should be exported even when mixed with invalid lines."""
        trace = tmp_path / "trace.jsonl"
        with open(trace, "w") as f:
            f.write("corrupt line\n")
            f.write(json.dumps(_make_record(assistant_text="valid")) + "\n")
            f.write("another bad line\n")
        result = export_main([str(trace)])
        assert result == 0
        out = capsys.readouterr().out
        assert "valid" in out


# ---------------------------------------------------------------------------
# Markdown export content tests
# ---------------------------------------------------------------------------


class TestExportMarkdown:
    """Test _export_markdown() renders correct content for different block types."""

    def test_basic_text_message(self):
        """A simple text exchange should render user and assistant content."""
        records = [_make_record(user_content="What is 2+2?", assistant_text="4")]
        md = _export_markdown(records)
        assert "What is 2+2?" in md
        assert "4" in md
        assert "## Turn 1" in md

    def test_token_summary(self):
        """Summary section should include accurate token counts."""
        records = [
            _make_record(turn=1, input_tokens=100, output_tokens=50),
            _make_record(turn=2, input_tokens=200, output_tokens=75),
        ]
        md = _export_markdown(records)
        assert "**Input tokens**: 300" in md
        assert "**Output tokens**: 125" in md
        assert "**Turns**: 2" in md

    def test_cache_tokens_displayed_when_nonzero(self):
        """Cache token lines should only appear when values are > 0."""
        records = [_make_record(cache_read=500, cache_create=100)]
        md = _export_markdown(records)
        assert "Cache read tokens" in md
        assert "Cache create tokens" in md

    def test_cache_tokens_omitted_when_zero(self):
        """Cache token lines should not appear when values are 0."""
        records = [_make_record(cache_read=0, cache_create=0)]
        md = _export_markdown(records)
        assert "Cache read tokens" not in md
        assert "Cache create tokens" not in md

    def test_multi_model_summary(self):
        """Summary should list all models used."""
        records = [
            _make_record(turn=1, model="claude-sonnet-4-20250514"),
            _make_record(turn=2, model="claude-opus-4-20250514"),
        ]
        md = _export_markdown(records)
        assert "claude-opus-4-20250514" in md
        assert "claude-sonnet-4-20250514" in md

    def test_tool_use_block(self):
        """Tool use blocks should show the tool name and input JSON."""
        records = [
            _make_record(
                assistant_text="Let me search for that.",
                tool_use={"name": "web_search", "input": {"query": "Claude Code"}},
            )
        ]
        md = _export_markdown(records)
        assert "**Tool Use**: `web_search`" in md
        assert '"query"' in md
        assert "Claude Code" in md

    def test_thinking_block(self):
        """Thinking blocks should be wrapped in <details> tags."""
        records = [_make_record(thinking="Let me reason about this...")]
        md = _export_markdown(records)
        assert "<details>" in md
        assert "Let me reason about this..." in md

    def test_tool_result_string(self):
        """Tool results as plain strings should render in code blocks."""
        tool_result = [{"type": "tool_result", "tool_use_id": "toolu_123", "content": "file contents here"}]
        records = [_make_record(tool_result=tool_result)]
        md = _export_markdown(records)
        assert "**Tool Result**" in md
        assert "file contents here" in md

    def test_tool_result_with_text_blocks(self):
        """Tool results containing text blocks should extract the text."""
        tool_result = [
            {
                "type": "tool_result",
                "tool_use_id": "toolu_456",
                "content": [{"type": "text", "text": "search results"}],
            }
        ]
        records = [_make_record(tool_result=tool_result)]
        md = _export_markdown(records)
        assert "search results" in md

    def test_content_as_list_with_text(self):
        """User message content as a list of text blocks."""
        records = [_make_record()]
        # Override user message to be a list
        records[0]["request"]["body"]["messages"][0]["content"] = [{"type": "text", "text": "multi-block prompt"}]
        md = _export_markdown(records)
        assert "multi-block prompt" in md

    def test_per_turn_token_usage(self):
        """Each turn should include per-turn token usage line."""
        records = [_make_record(input_tokens=100, output_tokens=50, cache_read=10)]
        md = _export_markdown(records)
        assert "in=100" in md
        assert "out=50" in md
        assert "cache_read=10" in md

    def test_duration_displayed(self):
        """Duration should appear in each turn header."""
        records = [_make_record(duration_ms=1234)]
        md = _export_markdown(records)
        assert "1234ms" in md


# ---------------------------------------------------------------------------
# JSON export tests
# ---------------------------------------------------------------------------


class TestExportJSON:
    """Test _export_json() produces correct structured output."""

    def test_basic_structure(self):
        """JSON export should contain expected top-level keys."""
        records = [_make_record()]
        data = json.loads(_export_json(records))
        assert len(data) == 1
        entry = data[0]
        assert "turn" in entry
        assert "model" in entry
        assert "messages" in entry
        assert "response" in entry
        assert "content" in entry["response"]
        assert "usage" in entry["response"]
        assert "stop_reason" in entry["response"]

    def test_system_prompt_included(self):
        """When system prompt is present, it should be in the JSON output."""
        records = [_make_record()]
        records[0]["request"]["body"]["system"] = [{"type": "text", "text": "You are helpful."}]
        data = json.loads(_export_json(records))
        assert data[0]["system"] == [{"type": "text", "text": "You are helpful."}]

    def test_system_prompt_absent(self):
        """When no system prompt, 'system' key should not appear."""
        records = [_make_record()]
        data = json.loads(_export_json(records))
        assert "system" not in data[0]

    def test_tools_included(self):
        """When tools are defined, they should appear in JSON output."""
        records = [_make_record()]
        records[0]["request"]["body"]["tools"] = [
            {"name": "read_file", "description": "Read a file", "input_schema": {}}
        ]
        data = json.loads(_export_json(records))
        assert len(data[0]["tools"]) == 1
        assert data[0]["tools"][0]["name"] == "read_file"

    def test_tools_absent(self):
        """When no tools, 'tools' key should not appear."""
        records = [_make_record()]
        data = json.loads(_export_json(records))
        assert "tools" not in data[0]

    def test_multiple_records_preserved(self):
        """Multiple records should all be present in the output."""
        records = [_make_record(turn=i, model=f"model-{i}") for i in range(5)]
        data = json.loads(_export_json(records))
        assert len(data) == 5
        for i, entry in enumerate(data):
            assert entry["turn"] == i
            assert entry["model"] == f"model-{i}"
