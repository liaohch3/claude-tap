"""Prompt snapshot extraction from trace records.

The proxy records each provider's native request body. This module keeps a
small provider-aware normalization layer for downstream tools that want the
prompt surface rather than the full traffic trace.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PromptTool:
    name: str
    description: str = ""
    schema: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PromptSnapshot:
    provider: str
    model: str
    system_prompt: str = ""
    developer_prompt: str = ""
    user_message: str = ""
    tools: tuple[PromptTool, ...] = ()
    turn: int | None = None
    request_id: str = ""
    path: str = ""
    upstream_base_url: str = ""
    captured_at: str = ""
    raw_request_body: dict[str, Any] = field(default_factory=dict)


def snapshot_from_records(records: list[dict[str, Any]]) -> PromptSnapshot:
    """Select the best prompt-bearing request and normalize it.

    Preference is intentionally simple and explainable: choose generation
    requests with explicit prompt material and the largest tool surface. This
    picks Claude Code's tool-bearing `/v1/messages` call and Codex's
    `/v1/responses` call while ignoring lightweight probes.
    """

    candidates: list[tuple[int, dict[str, Any]]] = []
    for record in records:
        body = _request_body(record)
        if not body:
            continue
        provider = infer_provider(record)
        if provider == "unknown":
            continue
        candidates.append((_score_record(record, provider), record))

    if not candidates:
        raise ValueError("no prompt-bearing request found in trace")

    _score, record = max(candidates, key=lambda item: item[0])
    provider = infer_provider(record)
    if provider == "anthropic":
        return _anthropic_snapshot(record)
    if provider == "openai":
        return _openai_snapshot(record)
    if provider == "gemini":
        return _gemini_snapshot(record)
    raise ValueError("no prompt-bearing request found in trace")


def infer_provider(record: dict[str, Any]) -> str:
    """Infer provider protocol from the trace path and request body."""

    req = record.get("request") if isinstance(record.get("request"), dict) else {}
    path = str(req.get("path") or "").split("?", 1)[0]
    body = _request_body(record)

    if path.startswith("/v1/messages") or path.startswith("/v1/complete"):
        return "anthropic"
    if path.startswith("/v1/responses") or path.startswith("/responses"):
        return "openai"
    if path.startswith(("/v1/chat/completions", "/chat/completions", "/v1/completions", "/completions")):
        return "openai"
    if path.startswith("/v1internal"):
        return "gemini" if _looks_like_gemini_body(body) else "unknown"
    if path.startswith("/model/"):
        return "anthropic"
    if "/models/" in path or path.startswith(("/v1beta/models", "/v1/models")):
        return "gemini"

    if "messages" in body and ("system" in body or "anthropic_version" in body):
        return "anthropic"
    if "instructions" in body or "input" in body:
        return "openai"
    if _looks_like_gemini_body(body):
        return "gemini"
    return "unknown"


def render_prompt_markdown(snapshot: PromptSnapshot) -> str:
    """Render a normalized prompt snapshot as comparison-oriented Markdown.

    Volatile capture metadata such as request IDs, timestamps, upstream URLs,
    and turn numbers belongs in trace/meta files. The prompt Markdown is meant
    to diff cleanly across CLI versions, so it contains only prompt-bearing
    content and tool definitions.
    """

    lines: list[str] = []

    _append_section(lines, "System Prompt", snapshot.system_prompt)
    _append_section(lines, "Developer Prompt", snapshot.developer_prompt)
    _append_section(lines, "User Message", snapshot.user_message)

    lines.append("# Tools")
    lines.append("")
    if not snapshot.tools:
        lines.append("_No tools captured._")
        lines.append("")
    for tool in sorted(snapshot.tools, key=lambda t: t.name):
        lines.append(f"## {tool.name or 'unnamed_tool'}")
        lines.append("")
        if tool.description:
            lines.append(_indent_markdown_headers(tool.description, levels=2))
            lines.append("")
        schema = tool.schema if tool.schema else tool.raw
        lines.append("```json")
        lines.append(json.dumps(schema, indent=2, ensure_ascii=False))
        lines.append("```")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _indent_markdown_headers(text: str, *, levels: int = 1) -> str:
    prefix = "#" * levels
    return "\n".join(f"{prefix}{line}" if line.startswith("#") else line for line in text.splitlines())


def _score_record(record: dict[str, Any], provider: str) -> int:
    body = _request_body(record)
    tools = _tools_for_provider(provider, body)
    system_text, developer_text, user_text = _prompt_text_for_provider(provider, body)
    path = str((record.get("request") or {}).get("path") or "")

    score = len(tools) * 10
    if system_text:
        score += 100
    if developer_text:
        score += 60
    if user_text:
        score += 20
    if path.endswith("/models") or "/models?" in path:
        score -= 200
    return score


def _anthropic_snapshot(record: dict[str, Any]) -> PromptSnapshot:
    body = _request_body(record)
    system_prompt, _developer_prompt, user_message = _prompt_text_for_provider("anthropic", body)
    tools = tuple(_anthropic_tools_from_body(body))
    return _base_snapshot(
        record,
        provider="anthropic",
        model=str(body.get("model") or ""),
        system_prompt=system_prompt,
        user_message=user_message,
        tools=tools,
    )


def _openai_snapshot(record: dict[str, Any]) -> PromptSnapshot:
    body = _request_body(record)
    system_prompt, developer_prompt, user_message = _prompt_text_for_provider("openai", body)
    tools = tuple(_openai_tools(body.get("tools")))
    return _base_snapshot(
        record,
        provider="openai",
        model=str(body.get("model") or ""),
        system_prompt=system_prompt,
        developer_prompt=developer_prompt,
        user_message=user_message,
        tools=tools,
    )


def _gemini_snapshot(record: dict[str, Any]) -> PromptSnapshot:
    body = _request_body(record)
    system_prompt, developer_prompt, user_message = _prompt_text_for_provider("gemini", body)
    tools = tuple(_gemini_tools(body.get("tools")))
    model = str(body.get("model") or _gemini_model_from_path(str((record.get("request") or {}).get("path") or "")))
    return _base_snapshot(
        record,
        provider="gemini",
        model=model,
        system_prompt=system_prompt,
        developer_prompt=developer_prompt,
        user_message=user_message,
        tools=tools,
    )


def _base_snapshot(
    record: dict[str, Any],
    *,
    provider: str,
    model: str,
    system_prompt: str = "",
    developer_prompt: str = "",
    user_message: str = "",
    tools: tuple[PromptTool, ...] = (),
) -> PromptSnapshot:
    req = record.get("request") if isinstance(record.get("request"), dict) else {}
    return PromptSnapshot(
        provider=provider,
        model=model,
        system_prompt=system_prompt,
        developer_prompt=developer_prompt,
        user_message=user_message,
        tools=tools,
        turn=record.get("turn") if isinstance(record.get("turn"), int) else None,
        request_id=str(record.get("request_id") or ""),
        path=str(req.get("path") or ""),
        upstream_base_url=str(record.get("upstream_base_url") or ""),
        captured_at=str(record.get("timestamp") or ""),
        raw_request_body=_request_body(record),
    )


def _request_body(record: dict[str, Any]) -> dict[str, Any]:
    req = record.get("request") if isinstance(record.get("request"), dict) else {}
    candidates: list[dict[str, Any]] = []
    body = req.get("body")
    if isinstance(body, dict):
        candidates.append(body)
    ws_events = req.get("ws_events")
    if isinstance(ws_events, list):
        candidates.extend(event for event in ws_events if isinstance(event, dict))
    if not candidates:
        return {}

    return max((_prompt_body(candidate) for candidate in candidates), key=_prompt_body_score)


def _prompt_body(body: dict[str, Any]) -> dict[str, Any]:
    nested = body.get("request")
    if isinstance(nested, dict) and _looks_like_gemini_body(nested):
        return nested
    return body


def _prompt_body_score(body: dict[str, Any]) -> int:
    score = 0
    for key, weight in (
        ("system", 100),
        ("instructions", 100),
        ("system_instruction", 100),
        ("systemInstruction", 100),
        ("messages", 40),
        ("input", 40),
        ("contents", 40),
        ("prompt", 40),
        ("tools", 20),
    ):
        if key in body:
            score += weight
    tools = body.get("tools")
    if isinstance(tools, list):
        score += len(tools)
    return score


def _looks_like_gemini_body(body: dict[str, Any]) -> bool:
    return any(key in body for key in ("contents", "system_instruction", "systemInstruction"))


def _tools_for_provider(provider: str, body: dict[str, Any]) -> list[PromptTool]:
    if provider == "anthropic":
        return _anthropic_tools_from_body(body)
    if provider == "openai":
        return _openai_tools(body.get("tools"))
    if provider == "gemini":
        return _gemini_tools(body.get("tools"))
    return []


def _prompt_text_for_provider(provider: str, body: dict[str, Any]) -> tuple[str, str, str]:
    legacy_prompt = body.get("prompt") if isinstance(body.get("prompt"), str) else ""
    if provider == "anthropic":
        return (
            _anthropic_system_text(body.get("system")),
            "",
            _join_text([_messages_text(body.get("messages"), {"user"}), legacy_prompt]),
        )
    if provider == "openai":
        input_value = body.get("input")
        messages = body.get("messages")
        developer = _join_text(
            [
                _input_text(input_value, {"developer"}),
                _messages_text(messages, {"developer"}),
            ]
        )
        system = _join_text(
            [
                str(body.get("instructions") or ""),
                _input_text(input_value, {"system"}),
                _messages_text(messages, {"system"}),
            ]
        )
        user = _join_text(
            [
                _input_text(input_value, {"user"}),
                _messages_text(messages, {"user"}),
                legacy_prompt,
            ]
        )
        return (system, developer, user)
    if provider == "gemini":
        return (
            _gemini_parts_text(body.get("system_instruction") or body.get("systemInstruction")),
            _contents_text(body.get("contents"), {"developer", "system"}),
            _contents_text(body.get("contents"), {"user"}),
        )
    return ("", "", "")


def _anthropic_system_text(system: Any) -> str:
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        return _join_text(_content_text(item) for item in system)
    return ""


def _messages_text(messages: Any, roles: set[str]) -> str:
    if not isinstance(messages, list):
        return ""
    parts = []
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") in roles:
            parts.append(_content_text(msg.get("content")))
    return _join_text(parts)


def _input_text(input_value: Any, roles: set[str]) -> str:
    if isinstance(input_value, str):
        return input_value if "user" in roles else ""
    if not isinstance(input_value, list):
        return ""
    parts: list[str] = []
    for item in input_value:
        if not isinstance(item, dict) or item.get("role") not in roles:
            continue
        parts.append(_content_text(item.get("content")))
    return _join_text(parts)


def _contents_text(contents: Any, roles: set[str]) -> str:
    if not isinstance(contents, list):
        return ""
    parts: list[str] = []
    for item in contents:
        if not isinstance(item, dict):
            continue
        role = item.get("role") or "user"
        if role not in roles:
            continue
        parts.append(_gemini_parts_text(item))
    return _join_text(parts)


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return content["text"]
        if isinstance(content.get("input_text"), str):
            return content["input_text"]
        if "parts" in content:
            return _gemini_parts_text(content)
        return ""
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            for key in ("text", "input_text", "output_text"):
                if isinstance(block.get(key), str):
                    parts.append(block[key])
                    break
            else:
                nested = block.get("content")
                if isinstance(nested, (str, list, dict)):
                    text = _content_text(nested)
                    if text:
                        parts.append(text)
    return _join_text(parts)


def _gemini_parts_text(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    parts = value.get("parts")
    if not isinstance(parts, list):
        return ""
    return _join_text(
        part.get("text") for part in parts if isinstance(part, dict) and isinstance(part.get("text"), str)
    )


def _anthropic_tools_from_body(body: dict[str, Any]) -> list[PromptTool]:
    tools = _anthropic_tools(body.get("tools"))
    tool_config = body.get("toolConfig")
    if isinstance(tool_config, dict):
        tools.extend(_bedrock_tool_config_tools(tool_config.get("tools")))
    return tools


def _anthropic_tools(tools: Any) -> list[PromptTool]:
    if not isinstance(tools, list):
        return []
    out: list[PromptTool] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        out.append(
            PromptTool(
                name=str(tool.get("name") or tool.get("type") or ""),
                description=str(tool.get("description") or ""),
                schema=tool.get("input_schema") if isinstance(tool.get("input_schema"), dict) else {},
                raw=tool,
            )
        )
    return out


def _bedrock_tool_config_tools(tools: Any) -> list[PromptTool]:
    if not isinstance(tools, list):
        return []
    out: list[PromptTool] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        spec = tool.get("toolSpec")
        if not isinstance(spec, dict):
            continue
        input_schema = spec.get("inputSchema")
        schema = input_schema.get("json") if isinstance(input_schema, dict) else {}
        out.append(
            PromptTool(
                name=str(spec.get("name") or ""),
                description=str(spec.get("description") or ""),
                schema=schema if isinstance(schema, dict) else {},
                raw=tool,
            )
        )
    return out


def _openai_tools(tools: Any) -> list[PromptTool]:
    if not isinstance(tools, list):
        return []
    out: list[PromptTool] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        if isinstance(tool.get("function"), dict):
            fn = tool["function"]
            raw = tool
        else:
            fn = tool
            raw = tool
        schema = fn.get("parameters") if isinstance(fn.get("parameters"), dict) else {}
        if not schema and isinstance(fn.get("input_schema"), dict):
            schema = fn["input_schema"]
        out.append(
            PromptTool(
                name=str(fn.get("name") or fn.get("type") or tool.get("type") or ""),
                description=str(fn.get("description") or ""),
                schema=schema,
                raw=raw,
            )
        )
    return out


def _gemini_tools(tools: Any) -> list[PromptTool]:
    if not isinstance(tools, list):
        return []
    out: list[PromptTool] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        declarations = tool.get("function_declarations") or tool.get("functionDeclarations")
        if isinstance(declarations, list):
            for decl in declarations:
                if not isinstance(decl, dict):
                    continue
                params = decl.get("parameters") if isinstance(decl.get("parameters"), dict) else {}
                out.append(
                    PromptTool(
                        name=str(decl.get("name") or ""),
                        description=str(decl.get("description") or ""),
                        schema=params,
                        raw=decl,
                    )
                )
        else:
            out.append(PromptTool(name=str(tool.get("name") or tool.get("type") or ""), raw=tool))
    return out


def _gemini_model_from_path(path: str) -> str:
    marker = "/models/"
    if marker not in path:
        return ""
    tail = path.split(marker, 1)[1].split("?", 1)[0]
    return tail.split(":", 1)[0]


def _join_text(parts: Any) -> str:
    return "\n\n".join(str(part).strip() for part in parts if isinstance(part, str) and part.strip())


def _append_section(lines: list[str], title: str, text: str) -> None:
    if not text:
        return
    lines.append(f"# {title}")
    lines.append("")
    lines.append(_indent_markdown_headers(text))
    lines.append("")
