from __future__ import annotations

import json
from pathlib import Path

from claude_tap.prompt_snapshot import infer_provider, render_prompt_markdown, snapshot_from_records


def _record(path: str, body: dict, *, turn: int = 1) -> dict:
    return {
        "timestamp": "2026-05-21T10:00:00+00:00",
        "request_id": f"req_{turn}",
        "turn": turn,
        "duration_ms": 1,
        "request": {"method": "POST", "path": path, "headers": {}, "body": body},
        "response": {"status": 200, "headers": {}, "body": {}},
        "upstream_base_url": "https://upstream.example.com",
    }


def test_anthropic_snapshot_selects_tool_bearing_request():
    light = _record(
        "/v1/messages?beta=true",
        {
            "model": "claude-haiku",
            "system": [{"type": "text", "text": "probe system"}],
            "messages": [{"role": "user", "content": "probe"}],
        },
        turn=1,
    )
    full = _record(
        "/v1/messages?beta=true",
        {
            "model": "claude-opus",
            "system": [{"type": "text", "text": "main system"}, {"type": "text", "text": "second block"}],
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
            "tools": [
                {
                    "name": "Bash",
                    "description": "Run shell commands",
                    "input_schema": {"type": "object", "properties": {"cmd": {"type": "string"}}},
                }
            ],
        },
        turn=2,
    )

    snapshot = snapshot_from_records([light, full])

    assert snapshot.provider == "anthropic"
    assert snapshot.model == "claude-opus"
    assert snapshot.turn == 2
    assert snapshot.system_prompt == "main system\n\nsecond block"
    assert snapshot.user_message == "hello"
    assert len(snapshot.tools) == 1
    assert snapshot.tools[0].name == "Bash"
    assert snapshot.tools[0].schema["properties"]["cmd"]["type"] == "string"


def test_openai_responses_snapshot_extracts_instructions_roles_and_tools():
    record = _record(
        "/v1/responses",
        {
            "model": "gpt-5.4",
            "instructions": "top instructions",
            "input": [
                {"role": "developer", "content": [{"type": "input_text", "text": "developer rules"}]},
                {"role": "system", "content": "system from input"},
                {"role": "user", "content": [{"type": "input_text", "text": "do the thing"}]},
            ],
            "tools": [
                {
                    "type": "function",
                    "name": "update_plan",
                    "description": "Update a plan",
                    "parameters": {"type": "object", "properties": {"plan": {"type": "array"}}},
                }
            ],
        },
    )

    snapshot = snapshot_from_records([record])

    assert snapshot.provider == "openai"
    assert snapshot.system_prompt == "top instructions\n\nsystem from input"
    assert snapshot.developer_prompt == "developer rules"
    assert snapshot.user_message == "do the thing"
    assert snapshot.tools[0].name == "update_plan"
    assert snapshot.tools[0].schema["properties"]["plan"]["type"] == "array"


def test_openai_chat_completions_snapshot_extracts_messages_and_function_tool():
    record = _record(
        "/v1/chat/completions",
        {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": "chat system"},
                {"role": "developer", "content": [{"type": "text", "text": "chat developer"}]},
                {"role": "user", "content": "chat user"},
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "lookup",
                        "description": "Lookup data",
                        "parameters": {"type": "object", "properties": {"id": {"type": "string"}}},
                    },
                }
            ],
        },
    )

    snapshot = snapshot_from_records([record])

    assert snapshot.provider == "openai"
    assert snapshot.system_prompt == "chat system"
    assert snapshot.developer_prompt == "chat developer"
    assert snapshot.user_message == "chat user"
    assert snapshot.tools[0].name == "lookup"
    assert snapshot.tools[0].schema["properties"]["id"]["type"] == "string"


def test_gemini_snapshot_extracts_system_contents_and_function_declarations():
    record = _record(
        "/v1beta/models/gemini-2.5-pro:streamGenerateContent?alt=sse",
        {
            "system_instruction": {"parts": [{"text": "gemini system"}]},
            "contents": [
                {"role": "user", "parts": [{"text": "hello "}, {"text": "gemini"}]},
                {"role": "model", "parts": [{"text": "ignored"}]},
            ],
            "tools": [
                {
                    "function_declarations": [
                        {
                            "name": "search",
                            "description": "Search things",
                            "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
                        }
                    ]
                }
            ],
        },
    )

    snapshot = snapshot_from_records([record])

    assert snapshot.provider == "gemini"
    assert snapshot.model == "gemini-2.5-pro"
    assert snapshot.system_prompt == "gemini system"
    assert snapshot.user_message == "hello\n\ngemini"
    assert snapshot.tools[0].name == "search"


def test_gemini_snapshot_treats_roleless_contents_as_user_text():
    record = _record(
        "/v1beta/models/gemini-2.5-pro:generateContent",
        {"contents": [{"parts": [{"text": "roleless prompt"}]}]},
    )

    snapshot = snapshot_from_records([record])

    assert snapshot.provider == "gemini"
    assert snapshot.user_message == "roleless prompt"


def test_gemini_snapshot_accepts_cli_camel_case_fields():
    record = _record(
        "/v1beta/models/gemini-3.1-pro-preview:streamGenerateContent?alt=sse",
        {
            "systemInstruction": {"parts": [{"text": "gemini cli system"}]},
            "contents": [{"role": "user", "parts": [{"text": "cli prompt"}]}],
            "tools": [
                {
                    "functionDeclarations": [
                        {
                            "name": "read_file",
                            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                        }
                    ]
                }
            ],
        },
    )

    snapshot = snapshot_from_records([record])

    assert snapshot.system_prompt == "gemini cli system"
    assert snapshot.user_message == "cli prompt"
    assert snapshot.tools[0].name == "read_file"


def test_code_assist_nested_request_snapshot_extracts_gemini_prompt():
    record = _record(
        "/v1internal:streamGenerateContent?alt=sse",
        {
            "request": {
                "systemInstruction": {"parts": [{"text": "code assist system"}]},
                "contents": [{"role": "user", "parts": [{"text": "code assist prompt"}]}],
                "tools": [
                    {
                        "functionDeclarations": [
                            {
                                "name": "read_file",
                                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                            }
                        ]
                    }
                ],
            }
        },
    )

    snapshot = snapshot_from_records([record])

    assert infer_provider(record) == "gemini"
    assert snapshot.provider == "gemini"
    assert snapshot.system_prompt == "code assist system"
    assert snapshot.user_message == "code assist prompt"
    assert snapshot.tools[0].name == "read_file"
    assert snapshot.tools[0].schema["properties"]["path"]["type"] == "string"


def test_legacy_completion_snapshot_extracts_prompt_field():
    anthropic = snapshot_from_records(
        [_record("/v1/complete", {"model": "claude-legacy", "prompt": "legacy anthropic prompt"})]
    )
    openai = snapshot_from_records(
        [_record("/v1/completions", {"model": "gpt-legacy", "prompt": "legacy openai prompt"})]
    )

    assert anthropic.provider == "anthropic"
    assert anthropic.user_message == "legacy anthropic prompt"
    assert openai.provider == "openai"
    assert openai.user_message == "legacy openai prompt"


def test_bedrock_snapshot_infers_anthropic_without_system_prompt():
    record = _record(
        "/model/us.anthropic.claude-sonnet-4-6-v1:0/invoke",
        {
            "anthropic_version": "bedrock-2023-05-31",
            "messages": [{"role": "user", "content": [{"text": "bedrock prompt"}]}],
        },
    )

    snapshot = snapshot_from_records([record])

    assert infer_provider(record) == "anthropic"
    assert snapshot.provider == "anthropic"
    assert snapshot.user_message == "bedrock prompt"


def test_bedrock_converse_snapshot_extracts_tool_config_tools():
    record = _record(
        "/model/us.anthropic.claude-sonnet-4-6-v1:0/converse",
        {
            "anthropic_version": "bedrock-2023-05-31",
            "messages": [{"role": "user", "content": [{"text": "bedrock prompt"}]}],
            "toolConfig": {
                "tools": [
                    {
                        "toolSpec": {
                            "name": "read_file",
                            "description": "Read a file",
                            "inputSchema": {"json": {"type": "object", "properties": {"path": {"type": "string"}}}},
                        }
                    }
                ]
            },
        },
    )

    snapshot = snapshot_from_records([record])

    assert snapshot.tools[0].name == "read_file"
    assert snapshot.tools[0].schema["properties"]["path"]["type"] == "string"


def test_websocket_snapshot_prefers_prompt_bearing_request_event():
    record = _record(
        "/v1/responses",
        {
            "type": "response.create",
            "model": "gpt-5",
            "input": [{"type": "function_call_output", "call_id": "call_1", "output": "{}"}],
        },
    )
    record["transport"] = "websocket"
    record["request"]["ws_events"] = [
        {"type": "response.create", "model": "gpt-5", "input": [], "tools": [], "generate": False},
        {
            "type": "response.create",
            "model": "gpt-5",
            "instructions": "ws instructions",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "ws prompt"}]}],
            "tools": [
                {
                    "type": "function",
                    "name": "exec_command",
                    "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}},
                }
            ],
        },
        record["request"]["body"],
    ]

    snapshot = snapshot_from_records([record])

    assert snapshot.provider == "openai"
    assert snapshot.system_prompt == "ws instructions"
    assert snapshot.user_message == "ws prompt"
    assert snapshot.tools[0].name == "exec_command"
    assert snapshot.raw_request_body["instructions"] == "ws instructions"


def test_provider_inference_can_fall_back_to_body_shape():
    assert infer_provider(_record("/custom", {"system": "s", "messages": []})) == "anthropic"
    assert infer_provider(_record("/custom", {"instructions": "i", "input": []})) == "openai"
    assert infer_provider(_record("/custom", {"system_instruction": {}, "contents": []})) == "gemini"
    assert infer_provider(_record("/custom", {"systemInstruction": {}, "contents": []})) == "gemini"
    assert infer_provider(_record("/custom", {"metadata": {}})) == "unknown"


def test_snapshot_rejects_traces_without_prompt_bearing_requests():
    records = [
        _record("/health", {"metadata": {}}),
        {"request": {"method": "GET", "path": "/v1/messages", "body": "not-json"}},
    ]

    try:
        snapshot_from_records(records)
    except ValueError as exc:
        assert str(exc) == "no prompt-bearing request found in trace"
    else:
        raise AssertionError("expected prompt-less trace to be rejected")


def test_snapshot_selection_penalizes_lightweight_model_probe():
    probe = _record("/v1/models", {"model": "probe", "instructions": "probe"}, turn=1)
    full = _record(
        "/v1/responses",
        {
            "model": "gpt-5",
            "instructions": "real instructions",
            "input": [{"role": "user", "content": "real user"}],
        },
        turn=2,
    )

    snapshot = snapshot_from_records([probe, full])

    assert snapshot.turn == 2
    assert snapshot.system_prompt == "real instructions"


def test_prompt_markdown_is_comparison_oriented_and_includes_raw_schema():
    snapshot = snapshot_from_records(
        [
            _record(
                "/v1/messages",
                {
                    "model": "claude",
                    "system": "# sys\ncontent",
                    "messages": [{"role": "user", "content": "hi"}],
                    "tools": [
                        {
                            "name": "Read",
                            "description": "# Read files",
                            "input_schema": {"type": "object"},
                        }
                    ],
                },
            )
        ]
    )

    out = render_prompt_markdown(snapshot)

    assert "# Prompt Snapshot" not in out
    assert "Request ID" not in out
    assert "Captured" not in out
    assert "# System Prompt" in out
    assert "## sys" in out
    assert "## Read" in out
    assert "### Read files" in out
    assert '"type": "object"' in out


def test_snapshot_extracts_nested_text_blocks_and_schema_fallbacks():
    record = _record(
        "/v1/responses",
        {
            "model": "gpt-5",
            "input": [
                {"role": "system", "content": {"input_text": "system dict text"}},
                {"role": "user", "content": ["plain", {"content": {"text": "nested"}}]},
            ],
            "tools": [
                "ignore",
                {"type": "function", "name": "schema_from_input", "input_schema": {"type": "object"}},
            ],
        },
    )

    snapshot = snapshot_from_records([record])

    assert snapshot.system_prompt == "system dict text"
    assert snapshot.user_message == "plain\n\nnested"
    assert snapshot.tools[0].name == "schema_from_input"
    assert snapshot.tools[0].schema == {"type": "object"}


def test_gemini_tools_accept_raw_tool_without_function_declarations():
    snapshot = snapshot_from_records(
        [
            _record(
                "/v1beta/models/gemini-pro:generateContent",
                {
                    "system_instruction": {"parts": [{"text": "system"}]},
                    "contents": [{"role": "user", "parts": [{"text": "hello"}]}],
                    "tools": ["ignore", {"type": "googleSearch"}],
                },
            )
        ]
    )

    assert snapshot.tools[0].name == "googleSearch"
    assert snapshot.tools[0].raw == {"type": "googleSearch"}


def test_prompt_md_export_format(tmp_path: Path):
    from claude_tap.export import export_main

    trace = tmp_path / "trace.jsonl"
    trace.write_text(
        json.dumps(
            _record(
                "/v1/messages",
                {
                    "model": "claude",
                    "system": "system text",
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "snapshot.prompt.md"

    rc = export_main([str(trace), "-o", str(out)])

    assert rc == 0
    text = out.read_text(encoding="utf-8")
    assert "# Prompt Snapshot" not in text
    assert "# System Prompt" in text
    assert "system text" in text
